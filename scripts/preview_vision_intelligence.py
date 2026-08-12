"""Debug CLI (Phase 14): chains the FULL pipeline through Phase 13
(FrameSource -> YOLO11nDetector -> ByteTrackAdapter -> DISOpticalFlowAdapter
-> CrowdMetricsEngine -> RiskStateMachine -> TriggerEngine, all constructed
ONCE before the loop) against people_clip.mp4. Whenever TriggerEngine
returns a non-NONE decision, constructs a real VisionInput from the SAME
frame that caused the trigger and calls REAL MiniCPMVisionModel.analyze()
inference — genuinely invoking Ollama, never simulated.

======================================================================
KNOWN ENVIRONMENT CAVEAT — same class of issue as Phase 13's thresholds
======================================================================
VLM_MODEL is a PRE-EXISTING env key (Phase 1 placeholder "placeholder-vlm").
If the developer's real .env still sets it, pydantic-settings' env-file
source overrides config.py's new default (minicpm-v4.6:q4_K_M) — and this
project NEVER edits the developer's real .env. This script prints the
ACTIVELY CONFIGURED VLM_MODEL at startup and warns loudly if it differs
from config.py's own authored default, so a stale-.env-caused
VLMUnavailableError is never mistaken for a code defect. See DECISIONS.md.

======================================================================
SYNTHETIC STRESS-TEST ADDENDUM
======================================================================
people_clip.mp4 is an established sparse/low-risk video. If zero triggers
fire on the real footage, this script continues with the SAME already-
constructed RiskStateMachine/TriggerEngine/MiniCPMVisionModel instances,
feeding artificially elevated risk_score values (same pattern as Phase
13's own preview script) to force at least one trigger-and-VLM-call cycle
end-to-end. The VLM call itself is STILL REAL inference — only the
risk_score/trigger CONDITION is synthetic (the image sent is the last
real video frame captured, since a real image is still required).

Usage: python scripts/preview_vision_intelligence.py <video_id>
"""

import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402

from app.core.config import REPO_ROOT, Settings, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.minicpm_vlm import MiniCPMVisionModel, VLMUnavailableError  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.risk_score import compute_risk_grid  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.roi_selection import select_roi  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150


def _print_active_vlm_model_diagnostic() -> None:
    live = settings.VLM_MODEL
    code_default = Settings.model_fields["VLM_MODEL"].default
    print(f"Active VLM_MODEL={live!r} (OLLAMA_BASE_URL={settings.OLLAMA_BASE_URL})")
    if live != code_default:
        print(
            f"  WARNING: active VLM_MODEL differs from config.py's authored "
            f"default ({code_default!r}) — the developer's real .env still "
            "sets this Phase-1-placeholder key, overriding the new default "
            "(this project never edits the developer's real .env). If "
            "MiniCPMVisionModel construction fails below, this is why — see "
            "this module's docstring and DECISIONS.md."
        )


def _compact_metrics(crowd_metrics, risk_score_value, risk_state_value) -> CompactCrowdMetricsSummary:
    return CompactCrowdMetricsSummary(
        risk_score=risk_score_value,
        risk_state=risk_state_value,
        max_density=float(crowd_metrics.core.density.grid.max()),
        max_pressure=crowd_metrics.core.pressure.max_pressure,
        pressure_units_disclaimer=crowd_metrics.core.pressure.units_disclaimer,
        congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
        reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
        bottleneck_signal_present=crowd_metrics.bottleneck is not None,
        density_confidence=crowd_metrics.core.density.estimation_confidence,
    )


def _print_vlm_call_result(label, trigger_decision, roi_bbox, result) -> None:
    print(
        f"  [{label}] frame {trigger_decision.frame_number} "
        f"t={trigger_decision.timestamp_seconds:.3f}s "
        f"trigger_type={trigger_decision.trigger_type.value} "
        f"reason=\"{trigger_decision.reason}\""
    )
    print(f"    roi_bbox={tuple(round(v, 1) for v in roi_bbox)}")
    print(f"    model_latency_seconds={result.model_latency_seconds:.2f}")
    if result.observations:
        for observation in result.observations:
            print(
                f"    observation: category={observation.category.value} "
                f"evidence_type={observation.evidence_type.value} "
                f"confidence={observation.confidence:.2f} "
                f"description={observation.description!r}"
            )
    else:
        print("    no observations")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_vision_intelligence.py <video_id>")
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

    print(f"Video: {video.original_filename} ({storage_path}), fps={fps}, {frame_width}x{frame_height}")
    _print_active_vlm_model_diagnostic()

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)
    risk_machine = RiskStateMachine()
    trigger_engine = TriggerEngine()

    try:
        vision_model = MiniCPMVisionModel()
    except VLMUnavailableError as exc:
        print(f"FATAL: MiniCPMVisionModel unavailable: {exc}")
        db.close()
        sys.exit(1)

    crowd_grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)

    real_triggers = {t: 0 for t in TriggerType}
    vlm_calls = 0
    latencies = []
    failures = []
    last_real_frame = None

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

                if trigger_decision.trigger_type != TriggerType.NONE:
                    real_triggers[trigger_decision.trigger_type] += 1

                    risk_grid = compute_risk_grid(
                        crowd_metrics.core.pressure, crowd_metrics.congestion,
                        crowd_metrics.bottleneck, crowd_metrics.reverse_flow,
                    )
                    roi_bbox = select_roi(risk_grid, crowd_grid, frame_width, frame_height)
                    compact_metrics = _compact_metrics(crowd_metrics, risk_result.risk_score, risk_result.state)
                    vision_input = VisionInput(
                        representative_frame=frame,
                        roi_crop_bbox=roi_bbox,
                        compact_metrics=compact_metrics,
                        trigger_reason=trigger_decision.reason,
                    )

                    try:
                        result = vision_model.analyze(vision_input)
                        vlm_calls += 1
                        latencies.append(result.model_latency_seconds)
                        _print_vlm_call_result("REAL", trigger_decision, roi_bbox, result)
                    except Exception as exc:
                        failures.append(str(exc))
                        print(f"  [REAL] VLM call FAILED: {exc}")

                last_real_frame = frame

            frames_processed += 1
            prev_frame = frame

    print()
    print("=== Real-video summary ===")
    print(f"Frames processed: {frames_processed}")
    print(f"Real triggers fired by type: {{{', '.join(f'{t.value}={c}' for t, c in real_triggers.items())}}}")
    print(f"Real VLM calls made: {vlm_calls}")

    total_real_triggers = sum(c for t, c in real_triggers.items() if t != TriggerType.NONE)

    # ==================================================================
    # SYNTHETIC STRESS-TEST ADDENDUM — forces a trigger if none fired
    # ==================================================================
    if total_real_triggers == 0:
        print()
        print(
            "No real triggers fired — given people_clip.mp4's established "
            "sparse/low-risk profile, this is PLAUSIBLE, HONEST behavior, "
            "not a bug. Running a SYNTHETIC stress-test addendum to still "
            "exercise a real end-to-end trigger-and-VLM-call cycle."
        )
        print("=== SYNTHETIC STRESS-TEST ADDENDUM (constructed risk_score, REAL VLM inference) ===")

        if last_real_frame is None:
            print("FATAL: no real frame was ever captured to use as the VLM image — aborting.")
            db.close()
            sys.exit(1)

        rise_elevated = settings.RISK_ELEVATED_THRESHOLD
        rise_critical = settings.RISK_CRITICAL_THRESHOLD
        gap = rise_critical - rise_elevated
        elevated_value = rise_elevated + (gap / 2.0)
        critical_value = rise_critical + gap + 1.0
        persistence_frames = settings.RISK_STATE_PERSISTENCE_FRAMES
        stage_frames = persistence_frames + 5

        # A synthetic risk grid sharing the argmax "hot cell" the ROI logic
        # will find — an arbitrary interior cell, purely for demonstration.
        synthetic_grid = np.zeros((crowd_grid.rows, crowd_grid.cols))
        hot_row, hot_col = crowd_grid.rows // 2, crowd_grid.cols // 2

        from app.pipeline.crowd_metrics import CrowdMetrics
        from app.pipeline.risk_score import RiskScoreResult

        frame_number = last_real_frame.frame_number + 1
        timestamp_seconds = last_real_frame.timestamp_seconds
        frame_dt = 1.0 / float(fps)
        synthetic_triggers = 0
        synthetic_vlm_calls = 0

        for stage_name, stage_value in (("ELEVATED", elevated_value), ("CRITICAL", critical_value)):
            for _ in range(stage_frames):
                timestamp_seconds += frame_dt
                risk_score_result = RiskScoreResult(
                    frame_number=frame_number, timestamp_seconds=timestamp_seconds,
                    risk_score=stage_value, confidence=1.0,
                    contributing_signals=["pressure"], sub_scores={"pressure": stage_value},
                )
                synthetic_crowd_metrics = CrowdMetrics(
                    frame_number=frame_number, timestamp_seconds=timestamp_seconds,
                    core=None, congestion=None, bottleneck=None, reverse_flow=None,
                    risk_score=risk_score_result, predictive_projection=None,
                )
                risk_result = risk_machine.update(synthetic_crowd_metrics)
                trigger_decision = trigger_engine.evaluate(synthetic_crowd_metrics, risk_result)

                if trigger_decision.trigger_type != TriggerType.NONE:
                    synthetic_triggers += 1
                    synthetic_grid[hot_row, hot_col] = stage_value
                    roi_bbox = select_roi(synthetic_grid, crowd_grid, frame_width, frame_height)
                    compact_metrics = CompactCrowdMetricsSummary(
                        risk_score=stage_value, risk_state=risk_result.state,
                        max_density=0.0, max_pressure=0.0,
                        pressure_units_disclaimer="PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS (SYNTHETIC)",
                        congested_cell_fraction=0.0, reverse_flow_cell_fraction=0.0,
                        bottleneck_signal_present=False, density_confidence=1.0,
                    )
                    vision_input = VisionInput(
                        representative_frame=last_real_frame,  # real image, synthetic trigger condition
                        roi_crop_bbox=roi_bbox,
                        compact_metrics=compact_metrics,
                        trigger_reason=trigger_decision.reason,
                    )
                    try:
                        result = vision_model.analyze(vision_input)
                        synthetic_vlm_calls += 1
                        latencies.append(result.model_latency_seconds)
                        _print_vlm_call_result("SYNTHETIC", trigger_decision, roi_bbox, result)
                    except Exception as exc:
                        failures.append(str(exc))
                        print(f"  [SYNTHETIC] VLM call FAILED: {exc}")

                frame_number += 1
            print(f"  (stage {stage_name} target reached: final state={risk_result.state.value})")

        print()
        print(f"Synthetic triggers fired: {synthetic_triggers}")
        print(f"Synthetic VLM calls made: {synthetic_vlm_calls}")

    print()
    print("=== Final summary ===")
    print(f"Real triggers fired: {total_real_triggers}, real VLM calls: {vlm_calls}")
    if latencies:
        print(f"Average measured VLM latency across ALL calls: {sum(latencies) / len(latencies):.2f}s "
              f"(min={min(latencies):.2f}s, max={max(latencies):.2f}s, n={len(latencies)})")
    else:
        print("Average measured VLM latency: n/a (no successful VLM calls)")
    if failures:
        print(f"Failures encountered ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print("No failures encountered.")

    db.close()


if __name__ == "__main__":
    main()
