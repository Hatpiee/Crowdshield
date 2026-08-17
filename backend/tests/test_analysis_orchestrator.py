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
from dataclasses import dataclass

from sqlalchemy.exc import OperationalError

from app.core import database
from app.core.config import settings
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.incident import Incident, IncidentLifecycleStatus
from app.models.processing_run import ProcessingRun, ProcessingRunStatus
from app.models.video import VideoAsset
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator
from app.pipeline.bytetrack_adapter import ByteTrackAdapter
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.crowd_metrics import CrowdMetricsEngine
from app.pipeline.decision_result import DecisionOutcome, DecisionResult, RecommendationType
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter
from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.minicpm_vlm import VLMUnavailableError
from app.pipeline.mp4_frame_source import MP4FrameSource
from app.pipeline.risk_score import compute_risk_grid
from app.pipeline.risk_state import RiskStateMachine
from app.pipeline.roi_selection import select_roi
from app.pipeline.trigger_engine import TriggerDecision, TriggerEngine, TriggerType
from app.pipeline.verification_result import VerificationResult
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


def test_transient_db_commit_failure_during_loop_a_does_not_crash_run(
    db_session, test_user, monkeypatch, make_processable_video
):
    """§29: "Live pipeline does not depend on DB availability for in-flight
    processing." Simulates ONE transient DB commit failure exactly at Loop
    A's periodic in-flight persistence checkpoint (progress/snapshot/
    heatmap) — a single dropped connection mid-run, not a permanently dead
    database — and confirms Loop A's own in-memory computation (detection/
    tracking/crowd intelligence/risk/trigger, all already computed BEFORE
    the failing commit) is unaffected: the run still processes every
    remaining frame and reaches COMPLETED, with only that one cycle's
    persistence skipped."""
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 5)
    video = make_processable_video(num_frames=20)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    real_session_local = database.SessionLocal  # TestSessionLocal, per conftest's own redirect
    state = {"orchestrator_db": None, "commit_count": 0}

    def _patched_session_local():
        real_db = real_session_local()
        if state["orchestrator_db"] is None:
            state["orchestrator_db"] = real_db
            real_commit = real_db.commit

            def _flaky_commit():
                state["commit_count"] += 1
                # commit #1 = RUNNING transition, #2 = pre-loop total_frames
                # commit, #3 = the FIRST periodic in-loop checkpoint — fail
                # exactly that one, then let every later commit succeed.
                if state["commit_count"] == 3:
                    raise OperationalError("synthetic transient DB failure", None, Exception("connection dropped"))
                return real_commit()

            real_db.commit = _flaky_commit
        return real_db

    monkeypatch.setattr(database, "SessionLocal", _patched_session_local)

    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)

    assert state["commit_count"] > 3, (
        "the periodic checkpoint never committed again after the injected "
        "failure — test setup invalid, the failure was never actually hit"
    )
    assert run.status == ProcessingRunStatus.COMPLETED, (
        f"a single transient DB commit failure crashed the entire run "
        f"(status={run.status}, error_message={run.error_message!r}) — "
        f"violates §29's 'does not depend on DB availability for in-flight "
        f"processing' guarantee"
    )
    assert fresh_session.status == SessionStatus.COMPLETED
    assert run.frames_processed == 20


def test_non_db_bug_during_periodic_checkpoint_still_fails_the_run_not_swallowed(
    db_session, test_user, monkeypatch, make_processable_video
):
    """§29 follow-up, Item 1: the periodic-checkpoint except clause is
    deliberately narrowed to sqlalchemy.exc.OperationalError/InterfaceError/
    TimeoutError, NOT a bare `except Exception`. A genuine data/logic bug
    (here: a synthetic TypeError raised from inside
    crowd_metrics_snapshot_service.persist_snapshot, standing in for a real
    defect — e.g. a bad field mapping) must NOT be reinterpreted as "transient
    DB issue, continuing" — it must still propagate to Decision E's FAILED
    path with a clear error_message, exactly like
    test_run_reaches_failed_on_unexpected_exception_with_real_error_message's
    existing guarantee for Loop A's detector call."""
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 5)
    video = make_processable_video(num_frames=20)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    from app.services import crowd_metrics_snapshot_service

    def _boom(*args, **kwargs):
        raise TypeError("synthetic real bug — not a DB error")

    monkeypatch.setattr(crowd_metrics_snapshot_service, "persist_snapshot", _boom)

    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()  # must NOT raise — Decision E's outermost catch-all still applies

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)

    assert run.status == ProcessingRunStatus.FAILED, (
        "a genuine non-DB bug must still fail the run — the narrowed except "
        "clause must not have silently swallowed it as 'transient DB issue'"
    )
    assert run.error_message is not None
    assert "synthetic real bug" in run.error_message
    assert fresh_session.status == SessionStatus.FAILED


def test_sustained_outage_across_multiple_consecutive_checkpoints_recovers_cleanly(
    db_session, test_user, monkeypatch, make_processable_video
):
    """§29 follow-up, Item 5: the original DB-failure test only proves a
    SINGLE isolated blip is absorbed. A real outage can span several
    consecutive periodic checkpoints — this fails the FIRST THREE
    CONSECUTIVE periodic-checkpoint commits (not just one), then lets the
    DB recover, and confirms db.rollback() after each failed cycle does
    NOT accumulate corrupted session state (e.g. a PendingRollbackError
    from a mishandled prior failed transaction) — the run still processes
    every frame and reaches COMPLETED once the outage ends."""
    monkeypatch.setattr(settings, "PROGRESS_UPDATE_INTERVAL_FRAMES", 2)
    video = make_processable_video(num_frames=20)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    real_session_local = database.SessionLocal
    state = {"orchestrator_db": None, "commit_count": 0}

    def _patched_session_local():
        real_db = real_session_local()
        if state["orchestrator_db"] is None:
            state["orchestrator_db"] = real_db
            real_commit = real_db.commit

            def _flaky_commit():
                state["commit_count"] += 1
                # commit #1=RUNNING, #2=pre-loop total_frames, #3-#5=the
                # FIRST THREE CONSECUTIVE periodic checkpoints — a
                # sustained outage, not a single blip — then recovery.
                if 3 <= state["commit_count"] <= 5:
                    raise OperationalError(
                        "synthetic sustained DB outage", None, Exception("connection refused")
                    )
                return real_commit()

            real_db.commit = _flaky_commit
        return real_db

    monkeypatch.setattr(database, "SessionLocal", _patched_session_local)

    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)

    assert state["commit_count"] > 5, (
        "the outage must have ended and later checkpoints must have "
        "resumed committing — test setup invalid otherwise"
    )
    assert run.status == ProcessingRunStatus.COMPLETED, (
        f"three CONSECUTIVE transient failures must still degrade "
        f"gracefully, not accumulate corrupted session state or crash the "
        f"run (status={run.status}, error_message={run.error_message!r})"
    )
    assert fresh_session.status == SessionStatus.COMPLETED
    assert run.frames_processed == 20


def test_terminal_status_write_survives_one_transient_failure_and_still_reaches_completed(
    db_session, test_user, monkeypatch, make_processable_video
):
    """§29 follow-up, Item 3: unlike mid-run persistence, the FINAL
    terminal-status commit has no "next cycle" to fall back on. Fails
    exactly the FIRST attempt of that commit (one transient blip), then
    lets it recover, and confirms the run's own new retry
    (_commit_with_retry) absorbs it — reaching COMPLETED, not
    misreporting a genuinely successful run as FAILED just because the
    LAST write hiccuped once."""
    from app.pipeline import analysis_orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "_TERMINAL_COMMIT_RETRY_DELAY_SECONDS", 0.01)

    video = make_processable_video()  # 10 frames, no periodic checkpoint fires (interval=30)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    real_session_local = database.SessionLocal
    state = {"orchestrator_db": None, "commit_count": 0}

    def _patched_session_local():
        real_db = real_session_local()
        if state["orchestrator_db"] is None:
            state["orchestrator_db"] = real_db
            real_commit = real_db.commit

            def _flaky_commit():
                state["commit_count"] += 1
                # commit #1=RUNNING, #2=pre-loop total_frames, #3=post-loop
                # frames_processed, #4=the FIRST attempt of the terminal
                # status write — fail exactly that attempt, succeed on the
                # automatic retry.
                if state["commit_count"] == 4:
                    raise OperationalError(
                        "synthetic transient DB failure", None, Exception("connection dropped")
                    )
                return real_commit()

            real_db.commit = _flaky_commit
        return real_db

    monkeypatch.setattr(database, "SessionLocal", _patched_session_local)

    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)

    assert state["commit_count"] == 5, "expected exactly one retried attempt on the terminal write"
    assert run.status == ProcessingRunStatus.COMPLETED, (
        f"a transient blip exactly at the terminal write must be absorbed "
        f"by the retry, not misreport a successful run as FAILED "
        f"(status={run.status}, error_message={run.error_message!r})"
    )
    assert fresh_session.status == SessionStatus.COMPLETED


def test_terminal_status_write_durable_outage_logs_critical_and_leaves_run_unresolved(
    db_session, test_user, monkeypatch, make_processable_video, caplog
):
    """§29 follow-up, Item 3 (worst case): the database is durably (not
    transiently) unreachable for the ENTIRE end-of-run window — every
    attempt of BOTH the primary terminal-status write (3 attempts) AND its
    own fallback FAILED-status write (3 more attempts) fails. This is the
    one honest residual case where Decision E's "never stuck in RUNNING/
    PROCESSING" guarantee cannot be kept from inside this method alone
    (see DECISIONS.md) — confirms it fails LOUDLY (a CRITICAL log, not a
    swallowed debug line) rather than silently, and that run() itself still
    never raises out to its caller even in this worst case."""
    from app.pipeline import analysis_orchestrator as orchestrator_module

    monkeypatch.setattr(orchestrator_module, "_TERMINAL_COMMIT_RETRY_DELAY_SECONDS", 0.01)

    video = make_processable_video()
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

    real_session_local = database.SessionLocal
    state = {"orchestrator_db": None, "commit_count": 0}

    def _patched_session_local():
        real_db = real_session_local()
        if state["orchestrator_db"] is None:
            state["orchestrator_db"] = real_db
            real_commit = real_db.commit

            def _always_failing_commit():
                state["commit_count"] += 1
                # commits #1-#3 (RUNNING / pre-loop total_frames / post-loop
                # frames_processed) succeed normally; commit #4 onward
                # (every attempt of BOTH the primary terminal write's 3
                # attempts AND the fallback FAILED-write's own 3 attempts)
                # fails — a genuinely DURABLE outage, not a transient blip.
                if state["commit_count"] >= 4:
                    raise OperationalError(
                        "synthetic durable DB outage", None, Exception("connection refused")
                    )
                return real_commit()

            real_db.commit = _always_failing_commit
        return real_db

    monkeypatch.setattr(database, "SessionLocal", _patched_session_local)

    with caplog.at_level(logging.CRITICAL):
        orchestrator = AnalysisOrchestrator(session.id)
        orchestrator.run()  # must NOT raise — even this worst case is caught

    # commit #4,5,6 = the 3 exhausted attempts of the PRIMARY terminal
    # write; #7,8,9 = the 3 exhausted attempts of the FALLBACK write.
    assert state["commit_count"] == 9, "expected exactly 3+3 exhausted retry attempts"
    assert any("GRACEFUL DEGRADATION EXHAUSTED" in record.message for record in caplog.records)
    assert any(record.levelno == logging.CRITICAL for record in caplog.records)

    # The honest residual limitation: neither COMPLETED nor FAILED could be
    # recorded — the run is left exactly where its last SUCCESSFUL commit
    # left it (RUNNING/PROCESSING), because a durably unreachable database
    # genuinely cannot be written to from inside this one method call.
    db_session.expire_all()
    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)
    assert run.status == ProcessingRunStatus.RUNNING
    assert fresh_session.status == SessionStatus.PROCESSING


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


def test_active_incident_not_auto_resolved_when_video_ends(
    db_session, test_user, make_processable_video
):
    """Master spec §34 gap-closing test (Testing Audit phase): Case #17
    "Active incident at video end (not auto-resolved)." No existing test
    ever exercised AnalysisOrchestrator._run_loop_b's own incident-
    correlation call (analysis_orchestrator.py's `if
    incident_service.is_decision_incident_worthy(...):
    incident_service.correlate_or_create_incident(...)`) — every prior
    orchestrator test's synthetic clip is deliberately too short/uniform to
    ever cross a real confirmed escalation (see this file's own top
    docstring), so Loop B's real code path into incident correlation was
    never actually run.

    Forces TWO real INCIDENT-outcome decisions through the REAL
    `_run_loop_b` (same "call it directly with a real (frame, crowd_metrics,
    risk_result) triple, fake only the LLM-backed Reasoner/Verifier"
    technique already established by this file's other Loop B tests) with
    trigger timestamps 10s apart (well inside
    INCIDENT_CORRELATION_WINDOW_SECONDS=120s) — the SAME two-decision
    pattern test_incident_correlation.py's own
    test_second_decision_within_window_correlates_and_transitions_to_active
    already proves takes an incident DETECTED -> ACTIVE, now proven to
    happen through the orchestrator's own real wiring instead of a direct
    service call. Then runs a REAL, SEPARATE, short `orchestrator.run()` on
    the same session to genuinely reach ProcessingRun/AnalysisSession
    COMPLETED ("the video ends") and confirms the incident is untouched —
    still ACTIVE, never silently resolved just because processing finished.
    """
    video = make_processable_video()
    frame, crowd_metrics, risk_result = _real_crowd_metrics_pair(video)
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session_service.start_session(db_session, session)

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
            raise VLMUnavailableError("simulated: isolates incident correlation, not VLM behavior")

    class _FakeReasonerIncident:
        def reason(self, evidence_package):
            return DecisionResult(
                decision_id=uuid.uuid4(),
                evidence_package_id=evidence_package.package_id,
                evidence_cited=["risk_score"],
                outcome=DecisionOutcome.INCIDENT,
                reasoning_summary="fake reasoner: forces a real INCIDENT outcome",
                recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
                recommendation_rationale="fake",
                projection_narrative=None,
                abstention_reason=None,
                confidence=evidence_package.confidence,
                binding_constraint=evidence_package.binding_constraint,
            )

    @dataclass
    class _FakePassingVerifier:
        def verify(self, decision, evidence_package):
            return VerificationResult(
                verification_id=uuid.uuid4(), decision_id=decision.decision_id, passed=True,
                confidence_consistency_ok=True, evidence_grounding_existence_ok=True,
                llm_checks=None, issues_found=[],
            )

    orchestrator = AnalysisOrchestrator(session.id)
    builder = EvidenceBuilder()

    for timestamp_seconds in (0.0, 10.0):
        trigger_decision = TriggerDecision(
            frame_number=int(timestamp_seconds * video.fps), timestamp_seconds=timestamp_seconds,
            trigger_type=TriggerType.RISK, reason="test: forced incident-worthy trigger",
        )
        orchestrator._run_loop_b(
            session.id, builder, _FakeVisionModelDown(), _FakeReasonerIncident(), _FakePassingVerifier(),
            frame, crowd_metrics, risk_result, trigger_decision, roi_bbox, compact_metrics,
        )

    incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(incidents) == 1, "the two correlated decisions must land on ONE incident, not two"
    assert incidents[0].lifecycle_status == IncidentLifecycleStatus.ACTIVE, (
        "two decisions 10s apart (well within INCIDENT_CORRELATION_WINDOW_SECONDS) "
        "through the REAL orchestrator must reach ACTIVE, exactly like "
        "test_incident_correlation.py's own service-level equivalent"
    )
    incident_id = incidents[0].id

    # "The video ends": a REAL, separate orchestrator.run() on the SAME
    # session, over the short synthetic clip already documented (top of
    # this file) as too short/uniform to ever fire a NEW real trigger —
    # so this exercises pure Loop A completion, isolated from the
    # incident-creation step above.
    orchestrator.run()

    run = _latest_run(db_session, session.id)
    fresh_session = db_session.get(AnalysisSession, session.id)
    assert run.status == ProcessingRunStatus.COMPLETED, "video processing itself must finish"
    assert fresh_session.status == SessionStatus.COMPLETED

    db_session.expire_all()
    fresh_incident = db_session.get(Incident, incident_id)
    assert fresh_incident.lifecycle_status == IncidentLifecycleStatus.ACTIVE, (
        "§19: an incident must NEVER be silently auto-resolved just because "
        "the video/processing run reached its own terminal COMPLETED state — "
        "resolution is exclusively an operator action"
    )
