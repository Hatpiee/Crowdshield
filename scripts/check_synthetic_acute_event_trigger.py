"""Fast, no-LLM sanity check (Reasoner Stability / Acute-Hazard E2E
validation phase): generates the synthetic BEFORE->EVENT->AFTERMATH video
fixture (tests/fixtures/synthetic_acute_event.py) and runs it through the
REAL deterministic pipeline (YOLO11nDetector -> ByteTrackAdapter ->
DISOpticalFlowAdapter -> CrowdMetricsEngine -> AcuteHazardDetector) — the
exact same components _run_loop_a uses in production — to confirm,
CHEAPLY (no Ollama calls, CPU-only), whether ACUTE_HAZARD genuinely fires
against this synthetic content BEFORE spending real VLM+LLM inference time
on the full E2E chain.

This does NOT prove anything about real blast footage — it only proves the
synthetic fixture is capable of producing a genuine, unforced ACUTE_HAZARD
trigger through the real fusion logic (quorum + spatial-discrimination
mitigation), on synthetic pixels honestly labeled as such.

Usage: python scripts/check_synthetic_acute_event_trigger.py
"""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import settings  # noqa: E402
from app.pipeline.acute_hazard_detector import AcuteHazardDetector, SIGNAL_ORDER  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from tests.fixtures.synthetic_acute_event import (  # noqa: E402
    FPS, HEIGHT, WIDTH,
    DEFAULT_AFTERMATH_FRAMES, DEFAULT_BASELINE_FRAMES, DEFAULT_EVENT_FRAMES,
    generate_frames, generate_synthetic_acute_event_mp4,
)

FIXTURE_PATH = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "synthetic_acute_event.mp4"


def main() -> None:
    print(f"Generating synthetic fixture at {FIXTURE_PATH} ...")
    total = generate_synthetic_acute_event_mp4(FIXTURE_PATH)
    print(
        f"Wrote {total} frames ({WIDTH}x{HEIGHT} @ {FPS}fps): "
        f"{DEFAULT_BASELINE_FRAMES} BEFORE, {DEFAULT_EVENT_FRAMES} EVENT, "
        f"{DEFAULT_AFTERMATH_FRAMES} AFTERMATH — CLEARLY SYNTHETIC, not real footage."
    )
    print(
        f"Active thresholds: ZSCORE={settings.ACUTE_HAZARD_ZSCORE_THRESHOLD} "
        f"MIN_CORROBORATING={settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS} "
        f"MIN_BASELINE_OBS={settings.ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS} "
        f"EMA_ALPHA={settings.ACUTE_HAZARD_BASELINE_EMA_ALPHA} "
        f"COOLDOWN_S={settings.ACUTE_HAZARD_COOLDOWN_SECONDS}"
    )

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=FPS)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=WIDTH, frame_height=HEIGHT)
    grid = CrowdGrid.from_frame_dimensions(WIDTH, HEIGHT)
    acute_detector = AcuteHazardDetector(grid)
    risk_machine = RiskStateMachine()
    trigger_engine = TriggerEngine()

    # Map frame_number -> stage label, by re-deriving the same generator's
    # stage boundaries (generate_frames() is deterministic/seeded).
    stage_by_frame = {i: stage for i, (stage, _frame) in enumerate(generate_frames())}

    fired = []
    print()
    print("=== per-frame (every frame) ===")
    prev_frame = None
    with MP4FrameSource(FIXTURE_PATH, frame_step=1) as source:
        for frame in source.frames():
            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = engine.update(tracking_result, motion_result, elapsed_seconds)
                risk_result = risk_machine.update(crowd_metrics)

                signal = acute_detector.update(
                    motion_result, crowd_metrics.core.flow, detection_result, prev_frame, frame
                )
                trigger_decision = trigger_engine.evaluate(crowd_metrics, risk_result, acute_hazard_signal=signal)

                stage = stage_by_frame.get(frame.frame_number, "?")
                marker = ""
                if trigger_decision.trigger_type == TriggerType.ACUTE_HAZARD:
                    fired.append((frame.frame_number, stage))
                    marker = "  <== ACUTE_HAZARD TRIGGER"
                if signal.is_acute_hazard or frame.frame_number % 10 == 0:
                    print(
                        f"  frame {frame.frame_number} [{stage}] t={frame.timestamp_seconds:.2f}s "
                        f"motion={signal.raw_values['motion_energy']:.2f} "
                        f"div={signal.raw_values['flow_divergence']:.4f} "
                        f"det_delta={signal.raw_values['detection_count_delta']:.0f} "
                        f"scene={signal.raw_values['scene_change']:.4f} "
                        f"corroborating={signal.corroborating_signals}{marker}"
                    )

            prev_frame = frame

    print()
    if fired:
        print(f"RESULT: ACUTE_HAZARD fired {len(fired)} time(s), at (frame_number, stage): {fired}")
        print("A genuine, unforced trigger through the real production fusion logic — safe to proceed to the full real VLM+Reasoner+Incident E2E run.")
        sys.exit(0)
    else:
        print("RESULT: ACUTE_HAZARD never fired. The synthetic fixture needs to be made a stronger/more spatially-localized signal before attempting the real E2E run.")
        sys.exit(1)


if __name__ == "__main__":
    main()
