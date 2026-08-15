"""AnalysisOrchestrator — roadmap Phase 19 (this project's Phase 20), master
spec §28. Binds the ALREADY-BUILT, ALREADY-TESTED pipeline stages (Phases
5-19) into the two-loop execution model behind `POST /sessions/{id}/start`:

Loop A (continuous, every frame, quantitative): Detection -> Tracking ->
Optical Flow -> Crowd Intelligence -> Risk State -> Trigger Engine, run on
this module's own background thread (see orchestration_launcher.py). It
NEVER blocks waiting for Loop B.

Loop B (trigger-only, expensive semantic): VLM -> EvidenceBuilder ->
Reasoner -> [if INCIDENT] Verifier -> Incident correlation, spawned as its
OWN separate thread per non-NONE trigger, capped by
MAX_CONCURRENT_SEMANTIC_ANALYSES (Decision B) via a non-blocking semaphore
acquire — a trigger that fires while the cap is reached is DROPPED (never
queued), with a clear log entry.

This module reimplements NO pipeline component's internal logic — every
step below is a direct call into an existing Phase 5-19 function/class
(the exact same composition already exercised, end-to-end, by
scripts/preview_incident_manager.py). This phase's own job is CORRECT
COMPOSITION and CONCURRENCY SAFETY, not new algorithms.
"""

import logging
import threading
import uuid
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core import database
from app.core.config import REPO_ROOT, settings
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.processing_run import ProcessingRun, ProcessingRunStatus
from app.models.video import VideoAsset
from app.pipeline.bytetrack_adapter import ByteTrackAdapter
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.crowd_metrics import CrowdMetrics, CrowdMetricsEngine
from app.pipeline.decision_result import DecisionOutcome
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter
from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.frame import Frame
from app.pipeline.minicpm_vlm import MiniCPMVisionModel, VLMUnavailableError
from app.pipeline.mp4_frame_source import MP4FrameSource
from app.pipeline.reasoner import LLMUnavailableError, Reasoner
from app.pipeline.risk_score import compute_risk_grid
from app.pipeline.risk_state import RiskStateMachine, RiskStateResult
from app.pipeline.roi_selection import select_roi
from app.pipeline.trigger_engine import TriggerDecision, TriggerEngine, TriggerType
from app.pipeline.verifier import LLMVerificationUnavailableError, Verifier
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput
from app.pipeline.yolo_detector import YOLO11nDetector
from app.services import (
    decision_service,
    evidence_service,
    heatmap_service,
    incident_service,
    risk_state_service,
    verification_service,
)

logger = logging.getLogger(__name__)


class VideoPreconditionError(Exception):
    """The video file/metadata required to actually process this session is
    missing or invalid. Raised inside run()'s own try/except so it is
    handled uniformly by Decision E (-> FAILED with a clear error_message),
    exactly like any other unexpected exception during processing."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _fresh_status(db: Session, session_id: uuid.UUID) -> SessionStatus | None:
    """Decision F: bypasses the ORM identity map entirely (a Core-style
    column-only query) so a status change committed by a DIFFERENT
    session/thread/request (e.g. the /cancel route, using its own
    request-scoped db session) is always genuinely re-read from Postgres —
    never a stale cached attribute on an AnalysisSession object this
    orchestrator's own long-lived session already loaded earlier."""
    return db.query(AnalysisSession.status).filter(AnalysisSession.id == session_id).scalar()


class AnalysisOrchestrator:
    """Constructed ONCE per session run. Deliberately CHEAP to construct —
    __init__ stores only the session_id and this run's own
    threading.Semaphore. ALL heavy work (opening the video, constructing
    the per-session pipeline components — real YOLO model load, real
    Ollama handshakes — and running Loop A) happens inside run(), which is
    the function actually executed ON THE BACKGROUND THREAD (see
    orchestration_launcher.py). This is a deliberate reading of Decision A
    ("the HTTP response still returns immediately with the same shape as
    today") over a literal-but-impractical alternative where __init__
    itself performs that real I/O on the calling (request) thread before
    the background thread is even spawned — see DECISIONS.md.
    """

    def __init__(self, session_id: uuid.UUID) -> None:
        self._session_id = session_id
        # Decision B: scoped to THIS orchestrator instance, i.e. THIS
        # session's own Loop A — each concurrently-processing session gets
        # its own independent cap, matching how every other stateful
        # component here (Tracker, RiskStateMachine, TriggerEngine, ...)
        # is already scoped to one session, never shared globally.
        self._semaphore = threading.Semaphore(settings.MAX_CONCURRENT_SEMANTIC_ANALYSES)

    def _latest_processing_run(self, db: Session) -> ProcessingRun | None:
        return (
            db.query(ProcessingRun)
            .filter(ProcessingRun.session_id == self._session_id)
            .order_by(ProcessingRun.created_at.desc())
            .first()
        )

    def run(self) -> None:
        """The main entry point — executed on the background thread.
        Decision E: every run reaches a terminal state, always. The
        entire body below is wrapped in one outermost try/except; any
        unexpected exception (including VideoPreconditionError) results
        in ProcessingRun.status=FAILED / AnalysisSession.status=FAILED
        with a real error_message — never left stuck in RUNNING/
        PROCESSING indefinitely."""
        db = database.SessionLocal()
        try:
            session = db.get(AnalysisSession, self._session_id)
            processing_run = self._latest_processing_run(db)
            if session is None or processing_run is None:
                logger.error(
                    "AnalysisOrchestrator: session_id=%s or its ProcessingRun "
                    "was not found — nothing to run",
                    self._session_id,
                )
                return

            processing_run.status = ProcessingRunStatus.RUNNING
            processing_run.started_at = _now()
            session.status = SessionStatus.PROCESSING
            db.commit()

            loop_b_threads, was_cancelled = self._run_loop_a(db, session, processing_run)

            # Decision D: wait for any still-running Loop B thread(s)
            # BEFORE finalizing — a started evidence/decision chain must
            # reach its proper conclusion, never be silently abandoned
            # because Loop A finished (or was cancelled) first. Naturally
            # bounded by the VLM/LLM/Verifier calls' own configured
            # timeouts.
            for thread in loop_b_threads:
                thread.join()

            processing_run.completed_at = _now()
            if was_cancelled:
                processing_run.status = ProcessingRunStatus.CANCELLED
                session.status = SessionStatus.CANCELLED
                logger.info("AnalysisOrchestrator: session_id=%s CANCELLED", self._session_id)
            else:
                processing_run.status = ProcessingRunStatus.COMPLETED
                session.status = SessionStatus.COMPLETED
                logger.info("AnalysisOrchestrator: session_id=%s COMPLETED", self._session_id)
            db.commit()

        except Exception as exc:
            logger.exception("AnalysisOrchestrator: session_id=%s FAILED", self._session_id)
            try:
                db.rollback()
                session = db.get(AnalysisSession, self._session_id)
                processing_run = self._latest_processing_run(db)
                if processing_run is not None:
                    processing_run.status = ProcessingRunStatus.FAILED
                    processing_run.completed_at = _now()
                    processing_run.error_message = str(exc)
                if session is not None:
                    session.status = SessionStatus.FAILED
                db.commit()
            except Exception:
                logger.exception(
                    "AnalysisOrchestrator: session_id=%s failed AGAIN while "
                    "recording its own FAILED state",
                    self._session_id,
                )
        finally:
            db.close()

    def _run_loop_a(
        self, db: Session, session: AnalysisSession, processing_run: ProcessingRun
    ) -> tuple[list[threading.Thread], bool]:
        """Returns (spawned Loop B threads, was_cancelled)."""
        video = db.get(VideoAsset, session.video_id)
        if video is None:
            raise VideoPreconditionError(f"VideoAsset {session.video_id} not found")

        storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
        if (
            not storage_path.exists()
            or not video.fps or video.fps <= 0
            or not video.width or not video.height
        ):
            raise VideoPreconditionError(
                f"Video {video.id} at {storage_path!s} is missing its file or "
                f"its fps/width/height metadata (fps={video.fps}, "
                f"width={video.width}, height={video.height}) — cannot process."
            )

        fps, frame_width, frame_height = video.fps, video.width, video.height

        # Frozen Decision: every ONE-per-session component is constructed
        # exactly ONCE, here, never per-frame. VLM/Reasoner/Verifier are
        # each documented (minicpm_vlm.py / reasoner.py / verifier.py) as
        # stateless and safe to "construct once and reuse freely" — built
        # here too (not per Loop B invocation) so their real Ollama
        # handshake cost (a `client.list()` round trip) is paid once per
        # session run, not once per trigger.
        detector = YOLO11nDetector()
        tracker = ByteTrackAdapter(fps=fps)
        optical_flow = DISOpticalFlowAdapter()
        crowd_metrics_engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)
        risk_machine = RiskStateMachine()
        trigger_engine = TriggerEngine()
        crowd_grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
        vision_model = MiniCPMVisionModel()
        reasoner = Reasoner()
        verifier = Verifier()
        builder = EvidenceBuilder()

        loop_b_threads: list[threading.Thread] = []
        prev_frame: Frame | None = None
        prev_risk_result: RiskStateResult | None = None
        last_heatmap_timestamp: float | None = None
        frame_counter = 0
        was_cancelled = False

        with MP4FrameSource(storage_path, frame_step=1) as source:
            metadata = source.get_metadata()
            processing_run.total_frames = metadata.frame_count
            db.commit()

            # Decision I: process as fast as the CPU allows — this is a
            # prerecorded MP4, no real-time playback pacing.
            for frame in source.frames():
                detection_result = detector.detect(frame)
                tracking_result = tracker.update(detection_result)

                crowd_metrics: CrowdMetrics | None = None
                if prev_frame is not None:
                    motion_result = optical_flow.compute(prev_frame, frame)
                    elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                    crowd_metrics = crowd_metrics_engine.update(
                        tracking_result, motion_result, elapsed_seconds
                    )

                    risk_result = risk_machine.update(crowd_metrics)
                    risk_state_service.record_transition_if_confirmed(
                        db, session.id, prev_risk_result, risk_result
                    )

                    trigger_decision = trigger_engine.evaluate(crowd_metrics, risk_result)
                    if trigger_decision.trigger_type != TriggerType.NONE:
                        spawned = self._maybe_spawn_loop_b(
                            session.id, builder, vision_model, reasoner, verifier,
                            frame, crowd_metrics, risk_result, trigger_decision,
                            crowd_grid, frame_width, frame_height,
                        )
                        if spawned is not None:
                            loop_b_threads.append(spawned)

                    prev_risk_result = risk_result

                frame_counter += 1

                # Decision F/G: one shared periodic checkpoint for
                # cancellation-checking, progress-column updates, AND
                # heatmap-due checking — never per-frame.
                if frame_counter % settings.PROGRESS_UPDATE_INTERVAL_FRAMES == 0:
                    current_status = _fresh_status(db, session.id)
                    if current_status == SessionStatus.CANCELLED:
                        # Decision F: stop promptly — no new frames
                        # processed, no new Loop B threads spawned. Any
                        # ALREADY-RUNNING Loop B thread is allowed to run
                        # to completion (joined by run(), not here).
                        was_cancelled = True
                        break

                    processing_run.frames_processed = frame_counter
                    processing_run.last_progress_update_at = _now()
                    db.commit()

                    if crowd_metrics is not None:
                        heatmap_due = (
                            last_heatmap_timestamp is None
                            or (frame.timestamp_seconds - last_heatmap_timestamp)
                            >= settings.HEATMAP_GENERATION_INTERVAL_SECONDS
                        )
                        if heatmap_due:
                            heatmap_service.generate_and_persist_heatmaps(
                                db, session.id, frame.frame_number, frame.timestamp_seconds,
                                crowd_metrics, frame_width, frame_height,
                            )
                            last_heatmap_timestamp = frame.timestamp_seconds

                prev_frame = frame

            processing_run.frames_processed = frame_counter
            processing_run.last_progress_update_at = _now()
            db.commit()

        return loop_b_threads, was_cancelled

    def _maybe_spawn_loop_b(
        self,
        session_id: uuid.UUID,
        builder: EvidenceBuilder,
        vision_model: MiniCPMVisionModel,
        reasoner: Reasoner,
        verifier: Verifier,
        frame: Frame,
        crowd_metrics: CrowdMetrics,
        risk_result: RiskStateResult,
        trigger_decision: TriggerDecision,
        crowd_grid: CrowdGrid,
        frame_width: int,
        frame_height: int,
    ) -> threading.Thread | None:
        if not self._semaphore.acquire(blocking=False):
            # Decision B: DROPPED, not queued — always logged with the
            # trigger's own reason/timestamp, never silently discarded.
            logger.warning(
                "AnalysisOrchestrator: Loop B DROPPED for session_id=%s "
                "(MAX_CONCURRENT_SEMANTIC_ANALYSES=%d already reached) — "
                "trigger_type=%s reason=%r timestamp=%.2fs",
                session_id, settings.MAX_CONCURRENT_SEMANTIC_ANALYSES,
                trigger_decision.trigger_type.value, trigger_decision.reason,
                trigger_decision.timestamp_seconds,
            )
            return None

        risk_grid = compute_risk_grid(
            crowd_metrics.core.pressure, crowd_metrics.congestion,
            crowd_metrics.bottleneck, crowd_metrics.reverse_flow,
        )
        roi_bbox = select_roi(risk_grid, crowd_grid, frame_width, frame_height)
        compact_metrics = CompactCrowdMetricsSummary(
            risk_score=risk_result.risk_score, risk_state=risk_result.state,
            max_density=float(crowd_metrics.core.density.grid.max()),
            max_pressure=crowd_metrics.core.pressure.max_pressure,
            pressure_units_disclaimer=crowd_metrics.core.pressure.units_disclaimer,
            congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
            reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
            bottleneck_signal_present=crowd_metrics.bottleneck is not None,
            density_confidence=crowd_metrics.core.density.estimation_confidence,
        )

        thread = threading.Thread(
            target=self._run_loop_b,
            args=(
                session_id, builder, vision_model, reasoner, verifier,
                frame, crowd_metrics, risk_result, trigger_decision, roi_bbox, compact_metrics,
            ),
            name=f"loop-b-{session_id}-{trigger_decision.frame_number}",
            daemon=False,
        )
        thread.start()
        return thread

    def _run_loop_b(
        self,
        session_id: uuid.UUID,
        builder: EvidenceBuilder,
        vision_model: MiniCPMVisionModel,
        reasoner: Reasoner,
        verifier: Verifier,
        frame: Frame,
        crowd_metrics: CrowdMetrics,
        risk_result: RiskStateResult,
        trigger_decision: TriggerDecision,
        roi_bbox: tuple[float, float, float, float],
        compact_metrics: CompactCrowdMetricsSummary,
    ) -> None:
        """Runs on its OWN thread, spawned by _maybe_spawn_loop_b. Decision
        C: constructs its OWN fresh DB session — never reuses Loop A's.
        Any exception here is caught and logged; it must never crash Loop
        A (which has already moved on) or leave the semaphore acquired."""
        db = database.SessionLocal()
        try:
            vision_input = VisionInput(
                representative_frame=frame, roi_crop_bbox=roi_bbox,
                compact_metrics=compact_metrics, trigger_reason=trigger_decision.reason,
            )
            vlm_call_succeeded = True
            vision_result = None
            try:
                vision_result = vision_model.analyze(vision_input)
            except VLMUnavailableError as exc:
                # §29's documented failure mode: caught specifically, never
                # crashes this Loop B chain — Evidence is marked incomplete
                # instead and the chain continues.
                vlm_call_succeeded = False
                logger.warning("AnalysisOrchestrator Loop B: VLM unavailable: %s", exc)

            package_result = builder.build(
                db=db, session_id=session_id, frame=frame, crowd_metrics=crowd_metrics,
                risk_state_result=risk_result, trigger_decision=trigger_decision, roi_bbox=roi_bbox,
                vision_result=vision_result, vlm_call_succeeded=vlm_call_succeeded,
                predictive_projection=crowd_metrics.predictive_projection,
            )
            persisted_package = evidence_service.persist_evidence_package(db, package_result)

            try:
                decision_result = reasoner.reason(package_result)
            except LLMUnavailableError as exc:
                logger.warning(
                    "AnalysisOrchestrator Loop B: LLM unavailable, no decision "
                    "persisted for package_id=%s: %s", persisted_package.id, exc,
                )
                return

            persisted_decision = decision_service.persist_decision_result(db, decision_result)

            if decision_result.outcome == DecisionOutcome.INCIDENT:
                try:
                    verification_service.run_verification_if_warranted(
                        db, verifier, decision_result, package_result
                    )
                except LLMVerificationUnavailableError as exc:
                    logger.warning(
                        "AnalysisOrchestrator Loop B: Verifier unavailable for "
                        "decision_id=%s: %s", persisted_decision.id, exc,
                    )

            db.expire_all()
            fresh_decision_row = decision_service.get_decision_result(db, persisted_decision.id)
            if incident_service.is_decision_incident_worthy(db, fresh_decision_row):
                incident_service.correlate_or_create_incident(
                    db, session_id, fresh_decision_row, persisted_package
                )
        except Exception:
            logger.exception(
                "AnalysisOrchestrator Loop B: unexpected error for session_id=%s "
                "trigger_type=%s", session_id, trigger_decision.trigger_type.value,
            )
        finally:
            db.close()
            self._semaphore.release()
