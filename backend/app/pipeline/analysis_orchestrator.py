"""AnalysisOrchestrator — roadmap Phase 19 (this project's Phase 20), master
spec §28. Binds the ALREADY-BUILT, ALREADY-TESTED pipeline stages (Phases
5-19) into the two-loop execution model behind `POST /sessions/{id}/start`:

Loop A (continuous, every frame, quantitative): Detection -> Tracking ->
Optical Flow -> Crowd Intelligence -> Risk State -> Trigger Engine, run on
this module's own background thread (see orchestration_launcher.py). It
NEVER blocks waiting for Loop B.

Loop B (trigger-only, expensive semantic): VLM -> EvidenceBuilder ->
Reasoner -> [if INCIDENT] Verifier -> Incident correlation, submitted for
every non-NONE trigger to a per-session SemanticAdmissionQueue (Decision B,
Semantic Admission Control phase — see semantic_admission_queue.py) backed
by MAX_CONCURRENT_SEMANTIC_ANALYSES persistent worker threads and a small
bounded, freshness-aware pending queue (SEMANTIC_QUEUE_MAX_DEPTH,
SEMANTIC_QUEUE_STALENESS_SECONDS). Submission is always non-blocking; a
trigger that cannot be served promptly may be queued briefly, may displace
an older still-queued trigger, or may ultimately be dropped as stale —
never silently, always logged.

This module reimplements NO pipeline component's internal logic — every
step below is a direct call into an existing Phase 5-19 function/class
(the exact same composition already exercised, end-to-end, by
scripts/preview_incident_manager.py). This phase's own job is CORRECT
COMPOSITION and CONCURRENCY SAFETY, not new algorithms.
"""

import logging
import time
import uuid
from collections import deque
from datetime import datetime, timezone

from sqlalchemy.exc import InterfaceError, OperationalError
from sqlalchemy.exc import TimeoutError as SQLAlchemyTimeoutError
from sqlalchemy.orm import Session

from app.core import database
from app.core.config import REPO_ROOT, settings
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.processing_run import ProcessingRun, ProcessingRunStatus
from app.models.video import VideoAsset
from app.pipeline.acute_hazard_detector import AcuteHazardDetector, AcuteHazardSignal
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
from app.pipeline.semantic_admission_queue import SemanticAdmissionQueue
from app.pipeline.trigger_engine import TriggerDecision, TriggerEngine, TriggerType
from app.pipeline.verifier import LLMVerificationUnavailableError, Verifier
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput
from app.pipeline.yolo_detector import YOLO11nDetector
from app.services import (
    crowd_metrics_snapshot_service,
    decision_service,
    evidence_service,
    heatmap_service,
    incident_service,
    risk_state_service,
    verification_service,
)

logger = logging.getLogger(__name__)

# §29 follow-up (Testing Audit phase): the SPECIFIC set of exceptions that
# genuinely indicate "the database is transiently unreachable" — NOT the
# broader sqlalchemy.exc.DBAPIError, which also covers IntegrityError/
# DataError/ProgrammingError (real data/logic bugs, e.g. a constraint
# violation from a genuine defect elsewhere in this code — those must keep
# propagating to Decision E's FAILED path, never be silently reinterpreted
# as "transient DB issue, continuing", or a recurring real bug would repeat
# forever, invisible, instead of ever surfacing). OperationalError covers
# connection drops/timeouts/"server closed the connection"; InterfaceError
# covers DBAPI-level "connection already closed"; SQLAlchemyTimeoutError
# covers connection-pool checkout timeouts (the pool itself exhausted/DB
# unreachable for new connections) — all three are genuinely about
# AVAILABILITY, not data correctness.
_TRANSIENT_DB_ERRORS = (OperationalError, InterfaceError, SQLAlchemyTimeoutError)

# Bounded retry specifically for the TERMINAL status write (Item 3 of the
# §29 follow-up): unlike mid-run periodic/per-frame persistence (which can
# safely skip one cycle and try again next cycle — see _run_loop_a), there
# is no "next cycle" for the FINAL commit — Loop A has already finished. A
# transient blip exactly at that moment deserves a few short retries before
# being treated as fatal, rather than immediately downgrading a genuinely
# successful run to FAILED. Still bounded: a truly, durably unreachable
# database cannot be written to no matter how long this waits — see
# DECISIONS.md's "Implementation-Discovered Constraints" for the honest
# residual limit this still leaves.
_TERMINAL_COMMIT_MAX_ATTEMPTS = 3
_TERMINAL_COMMIT_RETRY_DELAY_SECONDS = 1.0


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
    SemanticAdmissionQueue (which starts its small, fixed-size worker-thread
    pool immediately but performs no real I/O itself). ALL heavy work
    (opening the video, constructing
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
        # Decision B (Semantic Admission Control phase — REPLACES the prior
        # bare threading.Semaphore with a bounded, freshness-aware admission
        # queue; see semantic_admission_queue.py's own module docstring and
        # DECISIONS.md "Semantic Admission Control"): still scoped to THIS
        # orchestrator instance, i.e. THIS session's own Loop A — each
        # concurrently-processing session gets its own independent queue,
        # matching how every other stateful component here (Tracker,
        # RiskStateMachine, TriggerEngine, ...) is already scoped to one
        # session, never shared globally.
        self._semantic_queue = SemanticAdmissionQueue(
            max_concurrent=settings.MAX_CONCURRENT_SEMANTIC_ANALYSES,
            max_queue_depth=settings.SEMANTIC_QUEUE_MAX_DEPTH,
            staleness_seconds=settings.SEMANTIC_QUEUE_STALENESS_SECONDS,
        )

    def _latest_processing_run(self, db: Session) -> ProcessingRun | None:
        return (
            db.query(ProcessingRun)
            .filter(ProcessingRun.session_id == self._session_id)
            .order_by(ProcessingRun.created_at.desc())
            .first()
        )

    def _commit_with_retry(self, db: Session, description: str, apply_changes) -> None:
        """Item 3 of the §29 follow-up: bounded retry for the TERMINAL
        status write only (called from run(), never from the mid-run
        per-cycle persistence in _run_loop_a, which already has its own
        skip-and-continue semantics via the next cycle). Re-raises the last
        transient error once _TERMINAL_COMMIT_MAX_ATTEMPTS is exhausted —
        the caller (run()'s own try/except) is responsible for the
        honest last-resort handling of a durably unreachable database.

        REAL BUG CAUGHT WHILE TESTING THIS: `apply_changes` (a zero-arg
        callable that assigns the intended terminal status onto the ORM
        objects) is called before EVERY attempt, not just the first. A
        failed attempt's own necessary `db.rollback()` doesn't just clear
        the aborted transaction — it EXPIRES every object tracked by this
        session, reverting the in-memory `.status = COMPLETED` assignment
        made before the failed commit. A bare retried `db.commit()` alone
        (this method's first draft) silently commits an empty transaction
        on the next attempt — the retry "succeeds" but persists nothing,
        leaving the row exactly as it was before. Caught by
        test_terminal_status_write_survives_one_transient_failure_and_still_reaches_completed
        actually failing before this fix (status stayed RUNNING, not
        COMPLETED, after a supposedly-successful retry) — see
        DECISIONS.md."""
        last_exc: Exception | None = None
        for attempt in range(1, _TERMINAL_COMMIT_MAX_ATTEMPTS + 1):
            try:
                apply_changes()
                db.commit()
                return
            except _TRANSIENT_DB_ERRORS as exc:
                last_exc = exc
                db.rollback()
                logger.error(
                    "§29 GRACEFUL DEGRADATION: AnalysisOrchestrator %s commit "
                    "attempt %d/%d failed for session_id=%s — %s",
                    description, attempt, _TERMINAL_COMMIT_MAX_ATTEMPTS,
                    self._session_id, exc,
                )
                if attempt < _TERMINAL_COMMIT_MAX_ATTEMPTS:
                    time.sleep(_TERMINAL_COMMIT_RETRY_DELAY_SECONDS)
        assert last_exc is not None
        raise last_exc

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

            was_cancelled = self._run_loop_a(db, session, processing_run)

            # Decision D (Semantic Admission Control phase: now via
            # SemanticAdmissionQueue.close() rather than joining a list of
            # ephemeral threads — see that module's own docstring): wait
            # for any still-running AND any still-queued (but not yet
            # stale) Loop B work BEFORE finalizing — a started evidence/
            # decision chain must reach its proper conclusion, never be
            # silently abandoned because Loop A finished (or was cancelled)
            # first. Naturally bounded by the VLM/LLM/Verifier calls' own
            # configured timeouts.
            self._semantic_queue.close()

            terminal_run_status = ProcessingRunStatus.CANCELLED if was_cancelled else ProcessingRunStatus.COMPLETED
            terminal_session_status = SessionStatus.CANCELLED if was_cancelled else SessionStatus.COMPLETED

            def _apply_terminal_status() -> None:
                # Re-applied on EVERY retry attempt, not just the first —
                # see _commit_with_retry's own docstring for why a bare
                # retried db.commit() alone is not enough.
                processing_run.status = terminal_run_status
                processing_run.completed_at = processing_run.completed_at or _now()
                session.status = terminal_session_status

            # Item 3 (§29 follow-up): retried, not a single-shot commit — a
            # transient blip exactly here must not misreport a genuinely
            # successful/cancelled run as FAILED just because the LAST
            # write hiccuped.
            self._commit_with_retry(db, "terminal status write", _apply_terminal_status)
            logger.info(
                "AnalysisOrchestrator: session_id=%s %s",
                self._session_id, terminal_run_status.value,
            )

        except Exception as exc:
            logger.exception("AnalysisOrchestrator: session_id=%s FAILED", self._session_id)
            try:
                db.rollback()
                session = db.get(AnalysisSession, self._session_id)
                processing_run = self._latest_processing_run(db)

                def _apply_failed_status() -> None:
                    # Same re-apply-on-every-attempt requirement as
                    # _apply_terminal_status above.
                    if processing_run is not None:
                        processing_run.status = ProcessingRunStatus.FAILED
                        processing_run.completed_at = processing_run.completed_at or _now()
                        processing_run.error_message = str(exc)
                    if session is not None:
                        session.status = SessionStatus.FAILED

                self._commit_with_retry(db, "fallback FAILED-status write", _apply_failed_status)
            except Exception:
                # Item 3 (§29 follow-up): both the original terminal write
                # AND its own retried fallback write are now exhausted —
                # this run genuinely cannot reach a terminal state from
                # here. CRITICAL (not just logger.exception's default
                # ERROR) because this is the one honest residual case
                # where Decision E's "never stuck in RUNNING/PROCESSING"
                # guarantee CANNOT be kept from inside this method alone —
                # see DECISIONS.md's "Implementation-Discovered
                # Constraints" entry for why this cannot be fully closed
                # without an external reconciliation process, and why that
                # is out of scope here.
                logger.critical(
                    "§29 GRACEFUL DEGRADATION EXHAUSTED: AnalysisOrchestrator "
                    "session_id=%s could NOT record ANY terminal status "
                    "(neither the original outcome nor a fallback FAILED) "
                    "after %d retried attempts each — the database appears "
                    "durably unreachable, not merely transiently. This run "
                    "may be left stuck in RUNNING/PROCESSING and requires "
                    "manual/operational investigation.",
                    self._session_id, _TERMINAL_COMMIT_MAX_ATTEMPTS,
                )
        finally:
            db.close()

    def _run_loop_a(
        self, db: Session, session: AnalysisSession, processing_run: ProcessingRun
    ) -> bool:
        """Returns was_cancelled. Loop B work is submitted to
        self._semantic_queue (never joined/tracked here directly — see
        run()'s own call to self._semantic_queue.close())."""
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
        # Acute-Hazard Trigger Phase: one fresh detector per session, same
        # lifecycle discipline as every other stateful per-session component
        # above.
        acute_hazard_detector = AcuteHazardDetector(crowd_grid)
        vision_model = MiniCPMVisionModel()
        reasoner = Reasoner()
        verifier = Verifier()
        builder = EvidenceBuilder()

        # Acute-Hazard Trigger Phase: a small bounded ring buffer of recent
        # frames, so an ACUTE_HAZARD-triggered VLM request can include one
        # genuine "before" frame (see minicpm_vlm.py's optional 3rd image).
        # NOT consulted by RISK/FALLBACK/OPERATOR triggers — those paths are
        # unaffected. Bounded (not unbounded growth over a long video) —
        # holds at most ~ACUTE_HAZARD_CONTEXT_FRAME_LOOKBACK_SECONDS worth
        # of frames at this video's own fps.
        context_frame_buffer_size = max(
            1, round(fps * settings.ACUTE_HAZARD_CONTEXT_FRAME_LOOKBACK_SECONDS)
        )
        frame_history: deque[Frame] = deque(maxlen=context_frame_buffer_size)

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
                acute_hazard_signal: AcuteHazardSignal | None = None
                if prev_frame is not None:
                    motion_result = optical_flow.compute(prev_frame, frame)
                    elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                    crowd_metrics = crowd_metrics_engine.update(
                        tracking_result, motion_result, elapsed_seconds
                    )
                    # Acute-Hazard Trigger Phase: computed every frame,
                    # right alongside crowd_metrics — all four of its inputs
                    # (motion_result, crowd_metrics.core.flow, detection_result,
                    # the two Frame objects) are already in scope here for
                    # free, no new expensive computation added except its
                    # own cheap scene-change diff.
                    acute_hazard_signal = acute_hazard_detector.update(
                        motion_result, crowd_metrics.core.flow, detection_result,
                        prev_frame, frame,
                    )

                    risk_result = risk_machine.update(crowd_metrics)
                    try:
                        risk_state_service.record_transition_if_confirmed(
                            db, session.id, prev_risk_result, risk_result
                        )
                    except _TRANSIENT_DB_ERRORS:
                        # §29: "Live pipeline does not depend on DB
                        # availability for in-flight processing." risk_result
                        # is already computed in memory above — a transient
                        # DB failure persisting the transition must only
                        # cost THIS frame's persistence, never crash Loop A.
                        # db.rollback() clears the session's failed-
                        # transaction state so later commits can succeed
                        # again once the DB recovers. Deliberately narrow
                        # (NOT bare `except Exception`): a genuine data/logic
                        # bug here (e.g. IntegrityError from a real defect)
                        # must keep propagating to Decision E's FAILED path,
                        # never be silently misreported as "transient DB
                        # issue, continuing" — see DECISIONS.md.
                        logger.error(
                            "§29 GRACEFUL DEGRADATION: AnalysisOrchestrator "
                            "risk-state transition persistence failed for "
                            "session_id=%s at frame=%d — skipping this "
                            "transition's persistence and continuing",
                            self._session_id, frame.frame_number, exc_info=True,
                        )
                        db.rollback()

                    trigger_decision = trigger_engine.evaluate(
                        crowd_metrics, risk_result, acute_hazard_signal=acute_hazard_signal
                    )
                    if trigger_decision.trigger_type != TriggerType.NONE:
                        # Acute-Hazard Trigger Phase: the "before" frame is
                        # only ever attached for an ACUTE_HAZARD trigger —
                        # RISK/FALLBACK/OPERATOR pass None, unchanged from
                        # before this phase. frame_history[0] is the OLDEST
                        # frame currently in the bounded ring buffer, i.e.
                        # the frame closest to ACUTE_HAZARD_CONTEXT_FRAME_
                        # LOOKBACK_SECONDS ago.
                        context_frame = None
                        if (
                            trigger_decision.trigger_type == TriggerType.ACUTE_HAZARD
                            and len(frame_history) > 0
                        ):
                            context_frame = frame_history[0]
                        self._maybe_spawn_loop_b(
                            session.id, builder, vision_model, reasoner, verifier,
                            frame, crowd_metrics, risk_result, trigger_decision,
                            crowd_grid, frame_width, frame_height,
                            acute_hazard_signal, context_frame,
                        )

                    prev_risk_result = risk_result

                frame_history.append(frame)
                frame_counter += 1

                # Decision F/G: one shared periodic checkpoint for
                # cancellation-checking, progress-column updates, AND
                # heatmap-due checking — never per-frame.
                if frame_counter % settings.PROGRESS_UPDATE_INTERVAL_FRAMES == 0:
                    try:
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

                        # Gap 1 retrofit (Phase 21): a CrowdMetricsSnapshot row
                        # at the SAME periodic checkpoint already used for
                        # progress/heatmap-cadence — never per-frame. Added to
                        # the session (not yet committed) here so it lands in
                        # the SAME commit as the progress-column update below,
                        # rather than an extra round trip.
                        if crowd_metrics is not None:
                            crowd_metrics_snapshot_service.persist_snapshot(
                                db, session.id, crowd_metrics, risk_result
                            )

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
                                    crowd_metrics, frame_width, frame_height, frame.image,
                                )
                                last_heatmap_timestamp = frame.timestamp_seconds
                    except _TRANSIENT_DB_ERRORS:
                        # §29: "Live pipeline does not depend on DB
                        # availability for in-flight processing." Everything
                        # this checkpoint would persist (progress columns,
                        # crowd-metrics snapshot, heatmaps) is DERIVED from
                        # in-memory state already computed above — a
                        # transient DB failure here must only cost THIS
                        # cycle's persistence, never the whole run.
                        # db.rollback() clears the session's failed-
                        # transaction state so the NEXT checkpoint can commit
                        # normally once the DB recovers. Deliberately narrow
                        # (NOT bare `except Exception`): a genuine data/logic
                        # bug here must keep propagating to Decision E's
                        # FAILED path, never be silently misreported as
                        # "transient DB issue, continuing" — see
                        # DECISIONS.md.
                        logger.error(
                            "§29 GRACEFUL DEGRADATION: AnalysisOrchestrator "
                            "periodic persistence failed for session_id=%s "
                            "at frame=%d — skipping this cycle's persistence "
                            "and continuing",
                            self._session_id, frame_counter, exc_info=True,
                        )
                        db.rollback()

                prev_frame = frame

            processing_run.frames_processed = frame_counter
            processing_run.last_progress_update_at = _now()
            db.commit()

        return was_cancelled

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
        acute_hazard_signal: AcuteHazardSignal | None,
        context_frame: Frame | None,
    ) -> None:
        # Decision B (Semantic Admission Control phase): admission is now
        # delegated entirely to self._semantic_queue — submission itself is
        # ALWAYS accepted (never blocks Loop A) and is non-blocking; the
        # queue internally decides run-immediately vs. wait-behind-an-
        # active-task vs. evict-a-staler-queued-item vs. eventually-
        # drop-as-stale, all logged with the trigger's own reason/timestamp
        # inside semantic_admission_queue.py, never silently discarded.

        # Acute-Hazard Trigger Phase (decision 4, ROI selection): an
        # ACUTE_HAZARD trigger localizes ROI to where the ANOMALY is (the
        # acute detector's own per-cell |divergence| grid — the only
        # per-cell-spatial signal among its four), not where crowd pressure
        # is. select_roi is fully generic (any argmax-able grid), reused
        # unchanged. Every other trigger type keeps the existing
        # risk-grid-based ROI, unaffected.
        if trigger_decision.trigger_type == TriggerType.ACUTE_HAZARD and acute_hazard_signal is not None:
            roi_bbox = select_roi(
                acute_hazard_signal.localization_grid, crowd_grid, frame_width, frame_height
            )
        else:
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

        self._semantic_queue.submit(
            run=lambda: self._run_loop_b(
                session_id, builder, vision_model, reasoner, verifier,
                frame, crowd_metrics, risk_result, trigger_decision, roi_bbox, compact_metrics,
                acute_hazard_signal, context_frame,
            ),
            trigger_timestamp_seconds=trigger_decision.timestamp_seconds,
            label=(
                f"{trigger_decision.trigger_type.value}@{trigger_decision.timestamp_seconds:.2f}s "
                f"session={session_id}"
            ),
        )

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
        acute_hazard_signal: AcuteHazardSignal | None,
        context_frame: Frame | None,
    ) -> None:
        """Runs on one of self._semantic_queue's own persistent worker
        threads (submitted via _maybe_spawn_loop_b). Decision C: constructs
        its OWN fresh DB session — never reuses Loop A's. Any exception
        here is caught and logged; it must never crash Loop A (which has
        already moved on) or leave the worker thread that ran it stuck."""
        db = database.SessionLocal()
        try:
            vision_input = VisionInput(
                representative_frame=frame, roi_crop_bbox=roi_bbox,
                compact_metrics=compact_metrics, trigger_reason=trigger_decision.reason,
                # Acute-Hazard Trigger Phase: context_frame is already None
                # here for every RISK/FALLBACK/OPERATOR trigger (the caller
                # only ever resolves a non-None context_frame for
                # ACUTE_HAZARD — see _run_loop_a) — no extra guard needed.
                context_frame=context_frame,
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
                # Acute-Hazard Trigger Phase: EvidenceBuilder.build() itself
                # only ever attaches these when trigger_decision.trigger_type
                # == ACUTE_HAZARD (see evidence_builder.py) — safe to pass
                # unconditionally here.
                acute_hazard_signal=acute_hazard_signal,
                context_frame_timestamp_seconds=(
                    context_frame.timestamp_seconds if context_frame is not None else None
                ),
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
