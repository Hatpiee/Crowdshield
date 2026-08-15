"""Debug CLI (Phase 19): chains the FULL pipeline through Phase 18
(FrameSource -> ... -> EvidenceBuilder -> Reasoner -> Verifier, all
constructed ONCE before the loop) against people_clip.mp4, extended to call
correlate_or_create_incident() whenever is_decision_incident_worthy() is
True — genuine end-to-end construction, not simulated.

======================================================================
FORCING MULTIPLE REAL CORRELATABLE INCIDENT CYCLES
======================================================================
RiskStateMachine's RISK trigger only fires on a genuine UPWARD transition
(and CRITICAL is the highest reachable rank per Phase 13's Resolution 2 —
INCIDENT state is structurally unreachable), so a single ELEVATED->CRITICAL
escalation sequence can only ever produce ONE real RISK-triggered cycle at
CRITICAL. To exercise DETECTED->ACTIVE AND the ACTIVE self-loop for real
(Step 5's explicit requirement: "at least 3" correlatable cycles), this
script additionally uses `operator_requested=True` — an ALREADY-BUILT,
legitimate TriggerEngine mechanism (Phase 13, Decision #8: "a deliberate
human action that must never be silently suppressed," exempt from
cooldown) — to force additional real trigger -> VLM -> Reasoner ->
[Verifier] -> correlate cycles while risk state remains CRITICAL. Every
call in every cycle is still genuinely real inference; only the TRIGGER
CONDITION (like every synthetic-stress-test script since Phase 13) is
engineered.

Since the Reasoner's outcome (INCIDENT vs WATCH vs NO_INCIDENT) is real
LLM judgment, not deterministic, this script keeps firing additional
OPERATOR cycles (bounded by MAX_OPERATOR_ATTEMPTS) until at least 3
genuinely incident-worthy decisions have been collected, or the attempt
budget is exhausted — robust to real LLM variance, never fabricated.

======================================================================
KNOWN FINDING, EMPIRICALLY MEASURED — WHY A SYNTHETIC MARKER IS DRAWN
======================================================================
Real measurement across 4 real preview runs (~77 real trigger-worthy
decision cycles): only ~2.6% of real VLM calls against genuinely calm,
unmodified real frames from people_clip.mp4 returned a non-empty
observations list. The other ~97% correctly (not incorrectly) triggered
Phase 17's `critical_risk_no_visual_evidence` contradiction check and
deterministically abstained — the system refusing to fabricate an
INCIDENT correlation when there is no real visual evidence, exactly as
designed. This is a genuine safety property, not a bug, but it also means
brute-force retrying on unmodified real footage cannot reliably produce
3 correlated cycles within a single run in reasonable time.

Per explicit user decision, this script therefore draws a SMALL, FIXED,
CLEARLY-DOCUMENTED synthetic hazard marker onto the ROI region of the
chosen real frame before EVERY synthetic-stage VLM call (CRITICAL-1 and
every OPERATOR-N cycle) — same red-circle-marker technique and constants
already established for VLM test fixtures (`scripts/vlm_security_fixtures.py`,
Phase 15), extended here from "test fixture" to "demo-reliability" use.
This gives the VLM concrete, real (rendered) visual content to genuinely
analyze and describe, rather than asking it to hallucinate one. It is a
DELIBERATE ENGINEERING CHOICE for THIS DEMO SCRIPT ONLY — it does NOT
change should_abstain()/the contradiction check/EvidenceBuilder/Reasoner/
Verifier in any way, and it is NOT a claim that people_clip.mp4 organically
contains this hazard. See DECISIONS.md.

Usage: python scripts/preview_incident_manager.py <video_id>
"""

import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

from app.core.config import REPO_ROOT, Settings, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.incident import Incident  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetrics, CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.evidence_builder import EvidenceBuilder  # noqa: E402
from app.pipeline.frame import Frame  # noqa: E402
from app.pipeline.minicpm_vlm import MiniCPMVisionModel, VLMUnavailableError  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.reasoner import LLMUnavailableError, Reasoner  # noqa: E402
from app.pipeline.risk_score import RiskScoreResult, compute_risk_grid  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.roi_selection import select_roi  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.verifier import LLMVerificationUnavailableError, Verifier  # noqa: E402
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import (  # noqa: E402
    decision_service, evidence_service, incident_service, session_service, verification_service,
)

CONTIGUOUS_FRAME_COUNT = 150
MAX_OPERATOR_ATTEMPTS = 15
MIN_INCIDENT_WORTHY_CYCLES = 3

# Same constants as scripts/vlm_security_fixtures.py's _HAZARD_* (Phase 15)
# — reused here for consistency, not reinvented. Radius/center chosen to
# comfortably fit within people_clip.mp4's real 576x720 frame dimensions.
_HAZARD_CENTER = (300, 400)
_HAZARD_RADIUS = 60
_HAZARD_COLOR_BGR = (0, 0, 255)


def _with_synthetic_hazard_marker(frame: Frame) -> Frame:
    """Returns a NEW Frame with a copy of the image carrying a small, fixed
    red circle marker — see this module's docstring for why. Never mutates
    the original frame/image."""
    marked_image = frame.image.copy()
    cv2.circle(marked_image, _HAZARD_CENTER, _HAZARD_RADIUS, _HAZARD_COLOR_BGR, -1)
    return Frame(
        frame_number=frame.frame_number, timestamp_seconds=frame.timestamp_seconds,
        image=marked_image, width=frame.width, height=frame.height,
    )


def _print_active_config_diagnostic() -> None:
    for field_name in ("VLM_MODEL", "LLM_MODEL"):
        live = getattr(settings, field_name)
        code_default = Settings.model_fields[field_name].default
        print(f"Active {field_name}={live!r}")
        if live != code_default:
            print(f"  WARNING: active {field_name} differs from config.py's authored default ({code_default!r}).")
    print(f"INCIDENT_CORRELATION_WINDOW_SECONDS={settings.INCIDENT_CORRELATION_WINDOW_SECONDS}")


def _compact_metrics(crowd_metrics, risk_score_value, risk_state_value) -> CompactCrowdMetricsSummary:
    return CompactCrowdMetricsSummary(
        risk_score=risk_score_value, risk_state=risk_state_value,
        max_density=float(crowd_metrics.core.density.grid.max()),
        max_pressure=crowd_metrics.core.pressure.max_pressure,
        pressure_units_disclaimer=crowd_metrics.core.pressure.units_disclaimer,
        congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
        reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
        bottleneck_signal_present=crowd_metrics.bottleneck is not None,
        density_confidence=crowd_metrics.core.density.estimation_confidence,
    )


def _run_cycle(
    label, db, session_id, builder, vision_model, reasoner, verifier, frame, crowd_metrics, risk_result,
    trigger_decision, roi_bbox, compact_metrics, incident_worthy_decisions, failures,
):
    vision_input = VisionInput(
        representative_frame=frame, roi_crop_bbox=roi_bbox,
        compact_metrics=compact_metrics, trigger_reason=trigger_decision.reason,
    )
    vlm_call_succeeded = True
    vision_result = None
    try:
        vision_result = vision_model.analyze(vision_input)
    except Exception as exc:
        vlm_call_succeeded = False
        failures.append(f"[{label}] VLM: {exc}")
        print(f"  [{label}] VLM call FAILED: {exc}")

    package_result = builder.build(
        db=db, session_id=session_id, frame=frame, crowd_metrics=crowd_metrics,
        risk_state_result=risk_result, trigger_decision=trigger_decision, roi_bbox=roi_bbox,
        vision_result=vision_result, vlm_call_succeeded=vlm_call_succeeded,
        predictive_projection=crowd_metrics.predictive_projection,
    )
    persisted_package = evidence_service.persist_evidence_package(db, package_result)

    print(
        f"  [{label}] package_id={persisted_package.id} t={trigger_decision.timestamp_seconds:.2f}s "
        f"trigger_type={trigger_decision.trigger_type.value} reason=\"{trigger_decision.reason}\""
    )

    try:
        decision_result = reasoner.reason(package_result)
    except LLMUnavailableError as exc:
        failures.append(f"[{label}] Reasoner: {exc}")
        print(f"  [{label}] Reasoner call FAILED: {exc}")
        return None

    persisted_decision = decision_service.persist_decision_result(db, decision_result)
    print(f"    decision_id={persisted_decision.id} outcome={decision_result.outcome.value}")

    if decision_result.outcome.value == "INCIDENT":
        try:
            verification_outcome = verification_service.run_verification_if_warranted(
                db, verifier, decision_result, package_result
            )
            if verification_outcome.applicable:
                print(
                    f"    verification: passed={verification_outcome.verification_result.passed}"
                )
        except LLMVerificationUnavailableError as exc:
            failures.append(f"[{label}] Verifier: {exc}")
            print(f"  [{label}] Verifier call FAILED: {exc}")

    db.expire_all()
    fresh_decision_row = decision_service.get_decision_result(db, persisted_decision.id)
    worthy = incident_service.is_decision_incident_worthy(db, fresh_decision_row)
    print(f"    is_decision_incident_worthy={worthy}")
    if not worthy:
        return None

    incident = incident_service.correlate_or_create_incident(
        db, session_id, fresh_decision_row, persisted_package
    )
    print(
        f"    -> correlated into incident_id={incident.id} "
        f"lifecycle_status={incident.lifecycle_status.value}"
    )
    incident_worthy_decisions.append((persisted_decision.id, incident.id))
    return incident


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_incident_manager.py <video_id>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])
    db = SessionLocal()

    video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
    if video is None:
        print(f"No video found with id={video_id}")
        sys.exit(1)
    storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
    fps, frame_width, frame_height = video.fps, video.width, video.height

    if not storage_path.exists() or not fps or fps <= 0 or not frame_width or not frame_height:
        print("Video file/metadata missing.")
        sys.exit(1)

    user = db.query(User).first()
    if user is None:
        print("No user exists in the database.")
        sys.exit(1)

    session = session_service.create_session(db, video.id, user.id)
    print(f"Created REAL AnalysisSession id={session.id} (creator={user.email})")
    print(f"Video: {video.original_filename} ({storage_path}), fps={fps}, {frame_width}x{frame_height}")
    _print_active_config_diagnostic()

    try:
        vision_model = MiniCPMVisionModel()
        reasoner = Reasoner()
        verifier = Verifier()
    except (VLMUnavailableError, LLMUnavailableError, LLMVerificationUnavailableError) as exc:
        print(f"FATAL: pipeline component unavailable: {exc}")
        db.close()
        sys.exit(1)

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)
    risk_machine = RiskStateMachine()
    trigger_engine = TriggerEngine()
    builder = EvidenceBuilder()
    crowd_grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)

    incident_worthy_decisions = []
    failures = []
    # Deliberately NOT just "the temporally last frame": keep the TOP-3
    # REAL frames by detected/tracked-people count seen across the whole
    # real pass, and cycle through them across synthetic attempts below —
    # genuine real-image DIVERSITY (not fabricated content) gives the VLM
    # more/varied real visual material to work with across attempts,
    # rather than repeatedly re-sampling the exact same single image.
    top_frames: list[tuple[int, object, object]] = []  # (track_count, frame, crowd_metrics)

    print()
    print("=== REAL VIDEO (continuous, every frame; finds the top-3 most visually populated real frames) ===")
    prev_frame = None
    frames_processed = 0
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if frames_processed >= CONTIGUOUS_FRAME_COUNT:
                break
            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)
            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = engine.update(tracking_result, motion_result, elapsed_seconds)
                track_count = crowd_metrics.core.density.track_count
                top_frames.append((track_count, frame, crowd_metrics))
                top_frames.sort(key=lambda t: t[0], reverse=True)
                top_frames = top_frames[:3]
            frames_processed += 1
            prev_frame = frame

    print(f"Frames processed: {frames_processed}, top track_counts: {[t[0] for t in top_frames]}")

    if not top_frames:
        print("FATAL: no real frame captured.")
        db.close()
        sys.exit(1)

    best_frame, best_crowd_metrics = top_frames[0][1], top_frames[0][2]

    print()
    print("=== SYNTHETIC STRESS-TEST: escalate to CRITICAL, then force multiple correlatable cycles ===")
    rise_elevated = settings.RISK_ELEVATED_THRESHOLD
    rise_critical = settings.RISK_CRITICAL_THRESHOLD
    gap = rise_critical - rise_elevated
    elevated_value = rise_elevated + (gap / 2.0)
    critical_value = rise_critical + gap + 1.0
    stage_frames = settings.RISK_STATE_PERSISTENCE_FRAMES + 5

    synthetic_grid = np.zeros((crowd_grid.rows, crowd_grid.cols))
    hot_row, hot_col = crowd_grid.rows // 2, crowd_grid.cols // 2

    frame_number = best_frame.frame_number + 1
    timestamp_seconds = best_frame.timestamp_seconds
    frame_dt = 1.0 / float(fps)

    def _synthetic_metrics(stage_value):
        risk_score_result = RiskScoreResult(
            frame_number=frame_number, timestamp_seconds=timestamp_seconds, risk_score=stage_value,
            confidence=1.0, contributing_signals=["pressure", "congestion", "bottleneck", "reverse_flow"],
            sub_scores={k: stage_value for k in ("pressure", "congestion", "bottleneck", "reverse_flow")},
        )
        return CrowdMetrics(
            frame_number=frame_number, timestamp_seconds=timestamp_seconds,
            core=best_crowd_metrics.core, congestion=best_crowd_metrics.congestion,
            bottleneck=best_crowd_metrics.bottleneck, reverse_flow=best_crowd_metrics.reverse_flow,
            risk_score=risk_score_result, predictive_projection=best_crowd_metrics.predictive_projection,
        )

    # Stage 1: ELEVATED (establishes a real upward transition baseline).
    for _ in range(stage_frames):
        timestamp_seconds += frame_dt
        synthetic_crowd_metrics = _synthetic_metrics(elevated_value)
        risk_result = risk_machine.update(synthetic_crowd_metrics)
        trigger_engine.evaluate(synthetic_crowd_metrics, risk_result)
        frame_number += 1

    # Stage 2: CRITICAL — the real RISK-triggered upward transition.
    critical_trigger = None
    for _ in range(stage_frames):
        timestamp_seconds += frame_dt
        synthetic_crowd_metrics = _synthetic_metrics(critical_value)
        risk_result = risk_machine.update(synthetic_crowd_metrics)
        trigger_decision = trigger_engine.evaluate(synthetic_crowd_metrics, risk_result)
        if trigger_decision.trigger_type != TriggerType.NONE:
            critical_trigger = (trigger_decision, synthetic_crowd_metrics, risk_result)
        frame_number += 1

    if critical_trigger is not None:
        trigger_decision, synthetic_crowd_metrics, risk_result = critical_trigger
        synthetic_grid[hot_row, hot_col] = critical_value
        roi_bbox = select_roi(synthetic_grid, crowd_grid, frame_width, frame_height)
        compact_metrics = CompactCrowdMetricsSummary(
            risk_score=critical_value, risk_state=risk_result.state, max_density=0.0, max_pressure=0.0,
            pressure_units_disclaimer="PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS (SYNTHETIC)",
            congested_cell_fraction=0.0, reverse_flow_cell_fraction=0.0,
            bottleneck_signal_present=False, density_confidence=1.0,
        )
        _run_cycle(
            "CRITICAL-1", db, session.id, builder, vision_model, reasoner, verifier,
            _with_synthetic_hazard_marker(best_frame),
            synthetic_crowd_metrics, risk_result, trigger_decision, roi_bbox, compact_metrics,
            incident_worthy_decisions, failures,
        )

    # Force additional real cycles via operator_requested=True (Phase 13's
    # already-built, cooldown-exempt mechanism) while state remains
    # CRITICAL, until >= MIN_INCIDENT_WORTHY_CYCLES incident-worthy
    # decisions are collected or the attempt budget is exhausted.
    attempt = 0
    while len(incident_worthy_decisions) < MIN_INCIDENT_WORTHY_CYCLES and attempt < MAX_OPERATOR_ATTEMPTS:
        attempt += 1
        timestamp_seconds += frame_dt * 5  # a few seconds apart, well within the correlation window
        synthetic_crowd_metrics = _synthetic_metrics(critical_value)
        risk_result = risk_machine.update(synthetic_crowd_metrics)
        trigger_decision = trigger_engine.evaluate(synthetic_crowd_metrics, risk_result, operator_requested=True)
        frame_number += 1

        synthetic_grid[hot_row, hot_col] = critical_value
        roi_bbox = select_roi(synthetic_grid, crowd_grid, frame_width, frame_height)
        compact_metrics = CompactCrowdMetricsSummary(
            risk_score=critical_value, risk_state=risk_result.state, max_density=0.0, max_pressure=0.0,
            pressure_units_disclaimer="PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS (SYNTHETIC)",
            congested_cell_fraction=0.0, reverse_flow_cell_fraction=0.0,
            bottleneck_signal_present=False, density_confidence=1.0,
        )
        # Cycle through the top-3 real frames for genuine image diversity
        # across attempts (see note above) rather than resampling one
        # single real image repeatedly.
        cycle_frame = top_frames[attempt % len(top_frames)][1]
        _run_cycle(
            f"OPERATOR-{attempt}", db, session.id, builder, vision_model, reasoner, verifier,
            _with_synthetic_hazard_marker(cycle_frame),
            synthetic_crowd_metrics, risk_result, trigger_decision, roi_bbox, compact_metrics,
            incident_worthy_decisions, failures,
        )

    print()
    print(f"Incident-worthy decisions collected: {len(incident_worthy_decisions)} (attempts used: {attempt})")

    if not incident_worthy_decisions:
        print("No incident-worthy decisions were produced by real LLM judgment in this run — stopping.")
        db.close()
        sys.exit(0)

    incident_id = incident_worthy_decisions[0][1]

    def _print_detail(label):
        detail = incident_service.get_incident_detail(db, incident_id)
        print(f"--- {label}: incident_id={incident_id} ---")
        print(
            f"  lifecycle_status={detail.incident.lifecycle_status.value} "
            f"closure_reason={detail.incident.closure_reason.value if detail.incident.closure_reason else None} "
            f"priority={detail.incident.priority.value} acknowledged={detail.incident.acknowledged}"
        )
        print(f"  linked_evidence count={len(detail.linked_evidence)}")
        print(f"  latest_recommendation={detail.latest_recommendation}")
        print(f"  operator_actions count={len(detail.operator_actions)}")
        for action in detail.operator_actions:
            print(f"    {action.action_type.value} by={action.performed_by} at={action.created_at} notes={action.notes!r}")

    print()
    print("=== Incident detail BEFORE operator actions ===")
    _print_detail("before")

    print()
    print("=== Applying 2 real operator actions via the service layer ===")
    incident_row = db.get(Incident, incident_id)
    incident_row = incident_service.acknowledge_incident(db, incident_row, user.id, notes="Reviewing real preview incident")
    print(f"acknowledge_incident -> acknowledged={incident_row.acknowledged} by={incident_row.acknowledged_by}")
    incident_row = incident_service.resolve_incident(db, incident_row, user.id, notes="Confirmed handled in preview run")
    print(f"resolve_incident -> lifecycle_status={incident_row.lifecycle_status.value} closure_reason={incident_row.closure_reason.value}")

    print()
    print("=== Incident detail AFTER operator actions ===")
    _print_detail("after")

    print()
    print("=== Final summary ===")
    print(f"Total incident-worthy real decisions: {len(incident_worthy_decisions)}")
    all_incidents = incident_service.get_session_incidents(db, session.id)
    print(f"Total incidents for this session: {len(all_incidents)}")
    for inc in all_incidents:
        print(f"  incident_id={inc.id} lifecycle_status={inc.lifecycle_status.value} priority={inc.priority.value}")
    if failures:
        print(f"Failures encountered ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print("No failures encountered.")

    db.close()


if __name__ == "__main__":
    main()
