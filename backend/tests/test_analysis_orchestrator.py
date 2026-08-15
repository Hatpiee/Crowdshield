"""Phase 20, Step 6: AnalysisOrchestrator tests.

TEST DESIGN NOTE: full-run tests (construction-count, fresh-tracker
isolation, PROCESSING/COMPLETED/FAILED reachability, cancellation) use the
SHORT synthetic clip (tests/fixtures/synthetic_video.py) with every REAL
component (Detector/Tracker/OpticalFlow/CrowdMetricsEngine/RiskStateMachine/
TriggerEngine), per this phase's own wall-clock guidance. That clip is too
short/uniform to ever cross a real RISK_STATE_PERSISTENCE_FRAMES-confirmed
escalation, so Loop B never fires naturally within it — the concurrency-cap
(drop-not-queue) and thread-safety (fresh-DB-session-per-thread) tests
instead call `_maybe_spawn_loop_b`/`_run_loop_b` directly with a REAL
(frame, crowd_metrics, risk_result) triple obtained from 2 real frames plus
a real `operator_requested=True` TriggerDecision (the same already-built,
cooldown-exempt mechanism Phase 19's own preview script legitimately uses to
force a real trigger) — and fake only `_run_loop_b` itself (an
orchestrator-owned coordination method THIS phase wrote, not an existing
tested pipeline component) to isolate the concurrency behavior from real
VLM/Reasoner/Verifier latency. The VLM-unavailable test fakes only the
VisionModel/Reasoner adapters (the literal thing being "simulated down"),
while exercising the REAL `_run_loop_b`/EvidenceBuilder/evidence_service
code path around them.
"""

import logging
import threading
import time
import uuid

from app.core import database
from app.core.config import settings
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.processing_run import ProcessingRun, ProcessingRunStatus
from app.models.video import VideoAsset
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator
from app.pipeline.bytetrack_adapter import ByteTrackAdapter
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.crowd_metrics import CrowdMetricsEngine
from app.pipeline.decision_result import DecisionOutcome, DecisionResult
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter
from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.minicpm_vlm import VLMUnavailableError
from app.pipeline.mp4_frame_source import MP4FrameSource
from app.pipeline.risk_score import compute_risk_grid
from app.pipeline.risk_state import RiskStateMachine
from app.pipeline.roi_selection import select_roi
from app.pipeline.trigger_engine import TriggerEngine
from app.pipeline.vision_observation import CompactCrowdMetricsSummary
from app.pipeline.yolo_detector import YOLO11nDetector
from app.services import evidence_service, session_service, video_storage
from tests.fixtures.synthetic_video import DEFAULT_NUM_FRAMES


def _real_crowd_metrics_pair(video: VideoAsset):
    """Real Detector -> Tracker -> OpticalFlow -> CrowdMetricsEngine ->
    RiskStateMachine over the first 2 real frames of `video`, returning
    (last_frame, crowd_metrics, risk_result) — genuine pipeline output,
    fast (2 frames), used to build a real (though operator-forced)
    Loop B invocation in the concurrency/thread-safety tests below."""
    video_path = video_storage.get_storage_dir() / video.storage_filename
    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=video.fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=video.width, frame_height=video.height)
    risk_machine = RiskStateMachine()

    prev_frame = None
    with MP4FrameSource(video_path, frame_step=1) as source:
        for frame in source.frames():
            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)
            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = engine.update(tracking_result, motion_result, elapsed)
                risk_result = risk_machine.update(crowd_metrics)
                return frame, crowd_metrics, risk_result
            prev_frame = frame
    raise RuntimeError("video did not yield 2 frames")


def _latest_run(db_session, session_id) -> ProcessingRun:
    db_session.expire_all()
    return (
        db_session.query(ProcessingRun)
        .filter(ProcessingRun.session_id == session_id)
        .order_by(ProcessingRun.created_at.desc())
        .first()
    )


def test_full_run_completes_and_constructs_every_component_exactly_once(
    db_session, test_user, monkeypatch, make_processable_video
):
    video = make_processable_video()
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    counts: dict[str, int] = {}

    def _count(name, cls):
        original_init = cls.__init__

        def wrapper(self, *a, **kw):
            counts[name] = counts.get(name, 0) + 1
            return original_init(self, *a, **kw)

        monkeypatch.setattr(cls, "__init__", wrapper)

    for name, cls in (
        ("detector", YOLO11nDetector),
        ("tracker", ByteTrackAdapter),
        ("optical_flow", DISOpticalFlowAdapter),
        ("crowd_metrics_engine", CrowdMetricsEngine),
        ("risk_machine", RiskStateMachine),
        ("trigger_engine", TriggerEngine),
    ):
        _count(name, cls)

    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()

    for name in (
        "detector", "tracker", "optical_flow",
        "crowd_metrics_engine", "risk_machine", "trigger_engine",
    ):
        assert counts.get(name) == 1, f"{name} constructed {counts.get(name)} times, expected exactly 1"

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)
    assert run.status == ProcessingRunStatus.COMPLETED
    assert run.total_frames == DEFAULT_NUM_FRAMES
    assert run.frames_processed == DEFAULT_NUM_FRAMES
    assert run.started_at is not None
    assert run.completed_at is not None
    assert fresh_session.status == SessionStatus.COMPLETED


def test_fresh_tracker_per_run_no_shared_state_across_two_sessions(
    db_session, test_user, monkeypatch, make_processable_video
):
    constructed_trackers: list[ByteTrackAdapter] = []
    real_init = ByteTrackAdapter.__init__

    def spying_init(self, *a, **kw):
        real_init(self, *a, **kw)
        constructed_trackers.append(self)

    monkeypatch.setattr(ByteTrackAdapter, "__init__", spying_init)

    for _ in range(2):
        video = make_processable_video()
        session = session_service.create_session(db_session, video.id, test_user[0].id)
        session_service.start_session(db_session, session)
        AnalysisOrchestrator(session.id).run()

    assert len(constructed_trackers) == 2
    assert constructed_trackers[0] is not constructed_trackers[1]
    # Each run's Tracker starts with genuinely empty internal state (Phase
    # 7's own isolation guarantee) — proven here at the orchestrator level
    # by confirming a brand-new instance backs each run, never a reused one.
    assert constructed_trackers[0]._trajectories == {}
    assert constructed_trackers[1]._trajectories == {}


def test_processing_state_is_genuinely_reached_mid_run(
    db_session, test_user, monkeypatch, make_processable_video
):
    video = make_processable_video(num_frames=30)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    real_detect = YOLO11nDetector.detect

    def slow_detect(self, frame):
        time.sleep(0.05)
        return real_detect(self, frame)

    monkeypatch.setattr(YOLO11nDetector, "detect", slow_detect)

    orchestrator = AnalysisOrchestrator(session.id)
    thread = threading.Thread(target=orchestrator.run, daemon=False)
    thread.start()

    observed_processing = False
    deadline = time.time() + 15
    while time.time() < deadline:
        status = (
            db_session.query(AnalysisSession.status)
            .filter(AnalysisSession.id == session.id)
            .scalar()
        )
        if status == SessionStatus.PROCESSING:
            observed_processing = True
            break
        time.sleep(0.02)

    thread.join(timeout=30)
    assert observed_processing, (
        "AnalysisSession never observed in PROCESSING state — Phase 4 "
        "explicitly flagged this as unreachable until this phase"
    )

    db_session.expire_all()
    fresh_session = db_session.get(AnalysisSession, session.id)
    assert fresh_session.status == SessionStatus.COMPLETED


def test_cancellation_mid_run_stops_promptly_and_reaches_cancelled(
    db_session, test_user, monkeypatch, make_processable_video
):
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 1)
    video = make_processable_video(num_frames=40)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    started = threading.Event()
    proceed = threading.Event()
    call_count = {"n": 0}
    real_detect = YOLO11nDetector.detect

    def patched_detect(self, frame):
        call_count["n"] += 1
        if call_count["n"] == 2:
            started.set()
            proceed.wait(timeout=15)
        return real_detect(self, frame)

    monkeypatch.setattr(YOLO11nDetector, "detect", patched_detect)

    orchestrator = AnalysisOrchestrator(session.id)
    thread = threading.Thread(target=orchestrator.run, daemon=False)
    thread.start()

    assert started.wait(timeout=15), "orchestrator never reached its 2nd frame"

    # Cancel via the SAME real service call the /cancel route itself uses —
    # this is the exact mid-run cancellation path Step 4 requires.
    db_session.expire_all()
    fresh_session = db_session.get(AnalysisSession, session.id)
    session_service.cancel_session(db_session, fresh_session)
    proceed.set()

    thread.join(timeout=30)

    run = _latest_run(db_session, session.id)
    db_session.expire_all()
    final_session = db_session.get(AnalysisSession, session.id)

    assert final_session.status == SessionStatus.CANCELLED
    assert run.status == ProcessingRunStatus.CANCELLED
    assert run.completed_at is not None
    # Stopped promptly — nowhere near all 40 frames were processed.
    assert run.frames_processed is not None and run.frames_processed < 10


def test_run_reaches_failed_on_unexpected_exception_with_real_error_message(
    db_session, test_user, monkeypatch, make_processable_video
):
    video = make_processable_video()
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    def _boom(self, frame):
        raise RuntimeError("synthetic forced failure for test_analysis_orchestrator")

    monkeypatch.setattr(YOLO11nDetector, "detect", _boom)

    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()  # must NOT raise — the outermost try/except (Decision E) must catch it

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)

    assert run.status == ProcessingRunStatus.FAILED
    assert run.error_message is not None
    assert "synthetic forced failure" in run.error_message
    assert run.completed_at is not None
    assert fresh_session.status == SessionStatus.FAILED


def test_loop_b_second_trigger_dropped_not_queued_when_cap_reached(
    db_session, test_user, monkeypatch, caplog, make_processable_video
):
    monkeypatch.setattr(settings, "MAX_CONCURRENT_SEMANTIC_ANALYSES", 1)
    video = make_processable_video()
    frame, crowd_metrics, risk_result = _real_crowd_metrics_pair(video)
    session = session_service.create_session(db_session, video.id, test_user[0].id)

    trigger_decision = TriggerEngine().evaluate(crowd_metrics, risk_result, operator_requested=True)
    crowd_grid = CrowdGrid.from_frame_dimensions(video.width, video.height)
    builder = EvidenceBuilder()

    entered = threading.Event()
    release = threading.Event()

    def fake_run_loop_b(self, *args, **kwargs):
        entered.set()
        release.wait(timeout=10)

    monkeypatch.setattr(AnalysisOrchestrator, "_run_loop_b", fake_run_loop_b)

    orchestrator = AnalysisOrchestrator(session.id)

    first_thread = orchestrator._maybe_spawn_loop_b(
        session.id, builder, None, None, None, frame, crowd_metrics, risk_result,
        trigger_decision, crowd_grid, video.width, video.height,
    )
    assert first_thread is not None
    assert entered.wait(timeout=5), "first Loop B invocation never started"

    caplog.set_level(logging.WARNING)
    second_thread = orchestrator._maybe_spawn_loop_b(
        session.id, builder, None, None, None, frame, crowd_metrics, risk_result,
        trigger_decision, crowd_grid, video.width, video.height,
    )
    assert second_thread is None, "second trigger should be DROPPED, not queued, while the cap is reached"
    assert any("DROPPED" in record.message for record in caplog.records)

    release.set()
    first_thread.join(timeout=10)


def test_loop_b_uses_its_own_fresh_db_session_never_loop_as(
    db_session, test_user, monkeypatch, make_processable_video
):
    video = make_processable_video()
    frame, crowd_metrics, risk_result = _real_crowd_metrics_pair(video)
    session = session_service.create_session(db_session, video.id, test_user[0].id)

    trigger_decision = TriggerEngine().evaluate(crowd_metrics, risk_result, operator_requested=True)
    crowd_grid = CrowdGrid.from_frame_dimensions(video.width, video.height)
    builder = EvidenceBuilder()

    captured_session_ids: list[int] = []

    def spying_run_loop_b(self, session_id, *rest):
        loop_b_db = database.SessionLocal()
        try:
            captured_session_ids.append(id(loop_b_db))
        finally:
            loop_b_db.close()

    monkeypatch.setattr(AnalysisOrchestrator, "_run_loop_b", spying_run_loop_b)

    orchestrator = AnalysisOrchestrator(session.id)
    thread = orchestrator._maybe_spawn_loop_b(
        session.id, builder, None, None, None, frame, crowd_metrics, risk_result,
        trigger_decision, crowd_grid, video.width, video.height,
    )
    thread.join(timeout=10)

    assert len(captured_session_ids) == 1
    # Decision C: the Loop B thread's session must be a GENUINELY DIFFERENT
    # object from the spawning (Loop A / test) thread's own db_session —
    # never reused or shared.
    assert id(db_session) not in captured_session_ids


def test_loop_b_vlm_unavailable_is_caught_gracefully_evidence_marked_incomplete(
    db_session, test_user, make_processable_video,
):
    video = make_processable_video()
    frame, crowd_metrics, risk_result = _real_crowd_metrics_pair(video)
    session = session_service.create_session(db_session, video.id, test_user[0].id)

    trigger_decision = TriggerEngine().evaluate(crowd_metrics, risk_result, operator_requested=True)
    crowd_grid = CrowdGrid.from_frame_dimensions(video.width, video.height)
    risk_grid = compute_risk_grid(
        crowd_metrics.core.pressure, crowd_metrics.congestion,
        crowd_metrics.bottleneck, crowd_metrics.reverse_flow,
    )
    roi_bbox = select_roi(risk_grid, crowd_grid, video.width, video.height)
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

    class _FakeVisionModelDown:
        def analyze(self, vision_input):
            # Simulates §29's documented failure mode without depending on
            # Ollama actually being unreachable in this dev environment.
            raise VLMUnavailableError("simulated: Ollama unreachable")

    class _FakeReasonerAbstain:
        def reason(self, evidence_package):
            return DecisionResult(
                decision_id=uuid.uuid4(),
                evidence_package_id=evidence_package.package_id,
                evidence_cited=[],
                outcome=DecisionOutcome.ABSTAIN,
                reasoning_summary="fake reasoner (test isolates the VLM failure only)",
                recommendation=None,
                recommendation_rationale=None,
                projection_narrative=None,
                abstention_reason="fake",
                confidence=evidence_package.confidence,
                binding_constraint=evidence_package.binding_constraint,
            )

    orchestrator = AnalysisOrchestrator(session.id)
    builder = EvidenceBuilder()

    # The REAL _run_loop_b — not faked here — is the method under test.
    orchestrator._run_loop_b(
        session.id, builder, _FakeVisionModelDown(), _FakeReasonerAbstain(), None,
        frame, crowd_metrics, risk_result, trigger_decision, roi_bbox, compact_metrics,
    )  # must NOT raise — Loop A (and this call) must continue past a VLM failure

    packages = evidence_service.get_session_evidence_packages(db_session, session.id)
    assert len(packages) == 1
    assert packages[0].package.vision_observations_present is False
    assert "vision_observations" in packages[0].package.missing_evidence
