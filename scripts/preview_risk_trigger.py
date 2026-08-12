"""Debug CLI (Phase 13): chains the FULL pipeline through Phase 12
(FrameSource -> YOLO11nDetector -> ByteTrackAdapter -> DISOpticalFlowAdapter
-> CrowdMetricsEngine) against people_clip.mp4, feeding EVERY frame
continuously (no sparse sampling on the underlying pipeline — same
discipline verified in Phase 12's own clarification task) through ONE
`RiskStateMachine` and ONE `TriggerEngine`, both constructed once before the
loop (decision #10). Creates a real AnalysisSession and persists real
`RiskEvent` rows on every CONFIRMED transition — same genuine-persistence
pattern as Phase 12's preview_heatmaps.py, not throwaway.

======================================================================
KNOWN ENVIRONMENT CAVEAT — READ BEFORE INTERPRETING REAL-VIDEO OUTPUT
======================================================================
RISK_ELEVATED_THRESHOLD / RISK_CRITICAL_THRESHOLD / RISK_INCIDENT_THRESHOLD
are PRE-EXISTING env keys (Phase 1 placeholders, 0-1 scale). If the
developer's real .env still sets them, pydantic-settings' env-file source
overrides config.py's new, real, 0-100-scale defaults — and this project
NEVER edits the developer's real .env. This script prints the ACTUALLY
ACTIVE threshold values at startup and compares them against config.py's
own authored defaults, warning loudly if they differ, so stale-.env-driven
behavior is never mistaken for a code defect. See DECISIONS.md.

======================================================================
SYNTHETIC STRESS-TEST ADDENDUM
======================================================================
people_clip.mp4 is an established sparse/low-risk video (Phase 9-11's own
repeated finding) — it is ENTIRELY plausible NO escalation occurs on the
real footage. After the real-video section, this script continues feeding
the SAME already-constructed RiskStateMachine/TriggerEngine instances with
CLEARLY LABELED SYNTHETIC risk_score values (derived from whatever
thresholds are actually active, so this works correctly regardless of the
.env caveat above) sufficient to drive a real NORMAL -> ELEVATED ->
CRITICAL escalation and at least one RISK trigger firing end-to-end.

Usage: python scripts/preview_risk_trigger.py <video_id>
"""

import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import REPO_ROOT, Settings, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetrics, CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.risk_score import RiskScoreResult  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import risk_state_service, session_service  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150


def _print_active_threshold_diagnostic() -> None:
    live = {
        "RISK_ELEVATED_THRESHOLD": settings.RISK_ELEVATED_THRESHOLD,
        "RISK_CRITICAL_THRESHOLD": settings.RISK_CRITICAL_THRESHOLD,
        "RISK_INCIDENT_THRESHOLD": settings.RISK_INCIDENT_THRESHOLD,
    }
    code_defaults = {name: Settings.model_fields[name].default for name in live}
    print(
        f"Active thresholds: ELEVATED={live['RISK_ELEVATED_THRESHOLD']} "
        f"CRITICAL={live['RISK_CRITICAL_THRESHOLD']} "
        f"INCIDENT={live['RISK_INCIDENT_THRESHOLD']} "
        f"(RISK_STATE_FALL_HYSTERESIS_MARGIN={settings.RISK_STATE_FALL_HYSTERESIS_MARGIN}, "
        f"RISK_STATE_PERSISTENCE_FRAMES={settings.RISK_STATE_PERSISTENCE_FRAMES})"
    )
    if live != code_defaults:
        print(
            "  WARNING: active threshold values differ from config.py's authored "
            f"defaults ({code_defaults}) — the developer's real .env still sets "
            "these Phase-1-placeholder keys, overriding the new calibrated "
            "defaults (this project never edits the developer's real .env). "
            "Real-video results below reflect the ACTIVE values, not the "
            "intended calibrated ones — see this module's docstring and "
            "DECISIONS.md."
        )


def _cm(frame_number: int, timestamp_seconds: float, risk_score_value: float) -> CrowdMetrics:
    """Builds a minimal CrowdMetrics carrying only a risk_score — used for
    the SYNTHETIC stress-test addendum only (the real-video section uses
    genuine CrowdMetrics from CrowdMetricsEngine)."""
    risk_score = RiskScoreResult(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        risk_score=risk_score_value,
        confidence=1.0,
        contributing_signals=["pressure"],
        sub_scores={"pressure": risk_score_value},
    )
    return CrowdMetrics(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        core=None,
        congestion=None,
        bottleneck=None,
        reverse_flow=None,
        risk_score=risk_score,
        predictive_projection=None,
    )


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_risk_trigger.py <video_id>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])
    db = SessionLocal()

    video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
    if video is None:
        print(f"No video found with id={video_id}")
        sys.exit(1)
    storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
    fps = video.fps
    frame_width = video.width
    frame_height = video.height

    if not storage_path.exists():
        print(f"Video file not found on disk: {storage_path}")
        sys.exit(1)
    if not fps or fps <= 0 or not frame_width or not frame_height:
        print("Video is missing required metadata (fps/width/height).")
        sys.exit(1)

    user = db.query(User).first()
    if user is None:
        print("No user exists in the database — cannot create a real AnalysisSession.")
        sys.exit(1)

    session = session_service.create_session(db, video.id, user.id)
    print(f"Created REAL AnalysisSession id={session.id} (creator={user.email})")
    print(f"Video: {video.original_filename} ({storage_path}), fps={fps}, {frame_width}x{frame_height}")
    _print_active_threshold_diagnostic()

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)
    risk_machine = RiskStateMachine()
    trigger_engine = TriggerEngine()

    real_transitions = []
    real_triggers = {t: 0 for t in TriggerType}
    previous_risk_result = None
    last_frame_number = 0
    last_timestamp_seconds = 0.0

    print()
    print("=== REAL VIDEO (continuous, every frame) ===")
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

                risk_result = risk_machine.update(crowd_metrics)
                trigger_decision = trigger_engine.evaluate(crowd_metrics, risk_result)

                if risk_result.state_changed_this_frame:
                    event = risk_state_service.record_transition_if_confirmed(
                        db, session.id, previous_risk_result, risk_result
                    )
                    real_transitions.append(risk_result)
                    print(
                        f"  [TRANSITION] frame {risk_result.frame_number} "
                        f"t={risk_result.timestamp_seconds:.3f}s "
                        f"{event.previous_state.value} -> {event.new_state.value} "
                        f"risk_score={risk_result.risk_score:.2f} (persisted RiskEvent {event.id})"
                    )

                if trigger_decision.trigger_type != TriggerType.NONE:
                    real_triggers[trigger_decision.trigger_type] += 1
                    print(
                        f"  [TRIGGER] frame {trigger_decision.frame_number} "
                        f"t={trigger_decision.timestamp_seconds:.3f}s "
                        f"type={trigger_decision.trigger_type.value} reason=\"{trigger_decision.reason}\""
                    )

                previous_risk_result = risk_result
                last_frame_number = risk_result.frame_number
                last_timestamp_seconds = risk_result.timestamp_seconds

            frames_processed += 1
            prev_frame = frame

    print()
    print("=== Real-video summary ===")
    print(f"Frames processed: {frames_processed}")
    if real_transitions:
        print(f"Confirmed risk-state transitions: {len(real_transitions)}")
        for r in real_transitions:
            print(f"  -> {r.state.value} at frame {r.frame_number} (risk_score={r.risk_score:.2f})")
    else:
        print(
            "Confirmed risk-state transitions: 0 — state remained NORMAL for the "
            "entire real-video run. Given this footage's established sparse/"
            "low-risk profile (Phase 9-11's own repeated finding), this is "
            "PLAUSIBLE, HONEST behavior, not a bug — reported as observed, not "
            "manufactured."
        )
    print(f"Real-video triggers fired by type: {{{', '.join(f'{t.value}={c}' for t, c in real_triggers.items())}}}")

    # ==================================================================
    # SYNTHETIC STRESS-TEST ADDENDUM — NOT REAL VIDEO DATA
    # ==================================================================
    print()
    print("=== SYNTHETIC STRESS-TEST ADDENDUM (constructed values, NOT derived from real video) ===")
    rise_elevated = settings.RISK_ELEVATED_THRESHOLD
    rise_critical = settings.RISK_CRITICAL_THRESHOLD
    gap = rise_critical - rise_elevated
    elevated_value = rise_elevated + (gap / 2.0)  # safely between ELEVATED's and CRITICAL's rise thresholds
    critical_value = rise_critical + gap + 1.0  # safely above CRITICAL's rise threshold
    persistence_frames = settings.RISK_STATE_PERSISTENCE_FRAMES
    stage_frames = persistence_frames + 5  # a small margin past the exact confirmation point

    print(
        f"SYNTHETIC elevated_value={elevated_value:.2f} (between ELEVATED={rise_elevated} "
        f"and CRITICAL={rise_critical}), critical_value={critical_value:.2f} "
        f"(above CRITICAL), {stage_frames} frames per stage"
    )

    synthetic_transitions = []
    synthetic_triggers = {t: 0 for t in TriggerType}
    frame_number = last_frame_number + 1
    timestamp_seconds = last_timestamp_seconds
    frame_dt = 1.0 / float(fps)

    for stage_name, stage_value in (("ELEVATED", elevated_value), ("CRITICAL", critical_value)):
        for _ in range(stage_frames):
            timestamp_seconds += frame_dt
            crowd_metrics = _cm(frame_number, timestamp_seconds, stage_value)
            risk_result = risk_machine.update(crowd_metrics)
            trigger_decision = trigger_engine.evaluate(crowd_metrics, risk_result)

            if risk_result.state_changed_this_frame:
                event = risk_state_service.record_transition_if_confirmed(
                    db, session.id, previous_risk_result, risk_result
                )
                synthetic_transitions.append(risk_result)
                print(
                    f"  [SYNTHETIC TRANSITION] frame {risk_result.frame_number} "
                    f"t={risk_result.timestamp_seconds:.3f}s "
                    f"{event.previous_state.value} -> {event.new_state.value} "
                    f"risk_score={risk_result.risk_score:.2f} (persisted RiskEvent {event.id})"
                )

            if trigger_decision.trigger_type != TriggerType.NONE:
                synthetic_triggers[trigger_decision.trigger_type] += 1
                print(
                    f"  [SYNTHETIC TRIGGER] frame {trigger_decision.frame_number} "
                    f"t={trigger_decision.timestamp_seconds:.3f}s "
                    f"type={trigger_decision.trigger_type.value} reason=\"{trigger_decision.reason}\""
                )

            previous_risk_result = risk_result
            frame_number += 1
        print(f"  (stage {stage_name} target reached: final state={risk_result.state.value})")

    print()
    print("=== Synthetic-addendum summary ===")
    print(f"Confirmed risk-state transitions: {len(synthetic_transitions)}")
    for r in synthetic_transitions:
        print(f"  -> {r.state.value} at frame {r.frame_number} (risk_score={r.risk_score:.2f})")
    print(f"Synthetic triggers fired by type: {{{', '.join(f'{t.value}={c}' for t, c in synthetic_triggers.items())}}}")

    # ==================================================================
    # Round-trip verification (real DB, same as GET /sessions/{id}/risk)
    # ==================================================================
    print()
    print("=== Round-trip verification (direct service-layer query, same as the API route) ===")
    summary = risk_state_service.get_session_risk_summary(db, session.id)
    print(f"get_session_risk_summary current_state={summary.current_state}")
    print(f"get_session_risk_summary transition_history rows: {len(summary.transition_history)}")
    expected_total = len(real_transitions) + len(synthetic_transitions)
    print(
        f"Expected total confirmed transitions (real + synthetic): {expected_total} "
        f"-> matches DB row count: {len(summary.transition_history) == expected_total}"
    )
    for row in summary.transition_history:
        print(
            f"  RiskEvent {row.id}: frame={row.frame_number} t={row.timestamp_seconds:.3f}s "
            f"{row.previous_state.value} -> {row.new_state.value} "
            f"risk_score_at_transition={row.risk_score_at_transition:.2f}"
        )

    db.close()


if __name__ == "__main__":
    main()
