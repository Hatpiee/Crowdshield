"""Debug CLI: chain FrameSource + YOLO11nDetector + ByteTrackAdapter +
DISOpticalFlowAdapter + Phase 9's Density/Flow/Pressure + Phase 10's
Congestion/Bottleneck/Reverse Flow + this phase's Risk Score / Predictive
Projection on a contiguous span of frames from an already uploaded video.
Not part of the running application and not reachable via any HTTP route —
same standalone-CLI pattern as every prior preview_*.py script.

======================================================================
PIXEL-SPACE UNITS — READ THIS BEFORE READING ANY NUMBER BELOW
======================================================================
`risk_score` and `sub_scores["pressure"]` derive from Crowd Pressure, which
remains PIXEL-based (see Phase 9/10's own units disclaimers, carried
forward in DECISIONS.md). `PRESSURE_SCORE_REFERENCE_PX` is pixel-space-
native and uncalibrated against any real venue. The other three sub-scores
(congestion, bottleneck, reverse_flow) are already self-normalizing
fractions/ratios and do NOT carry this units caveat.
======================================================================

This is a SPARSE, LIGHTLY CONGESTED video (Phase 9/10's own established
finding on people_clip.mp4) — LOW risk scores throughout are EXPECTED,
CORRECT behavior here, not a bug. This script reports honestly whatever is
actually observed.

Usage: python scripts/preview_risk_score_projection.py <video_id>
"""

import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bottleneck import BottleneckDetector  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.congestion import compute_congestion_field  # noqa: E402
from app.pipeline.core_crowd_metrics import compute_core_crowd_metrics  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.predictive_projection import PressureProjector  # noqa: E402
from app.pipeline.reverse_flow import ReverseFlowDetector  # noqa: E402
from app.pipeline.risk_score import compute_risk_score  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150

PIXEL_UNITS_NOTE = (
    "[risk_score's pressure sub-score is PIXEL-SPACE, NOT METERS-calibrated; "
    "congestion/bottleneck/reverse_flow sub-scores are self-normalizing "
    "fractions with no such caveat — see DECISIONS.md]"
)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_risk_score_projection.py <video_id>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])

    db = SessionLocal()
    try:
        video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
        if video is None:
            print(f"No video found with id={video_id}")
            sys.exit(1)
        storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
        original_filename = video.original_filename
        fps = video.fps
    finally:
        db.close()

    if not storage_path.exists():
        print(f"Video file not found on disk: {storage_path}")
        sys.exit(1)
    if not fps or fps <= 0:
        print(f"Video has no valid stored fps ({fps!r}) — cannot construct a tracker.")
        sys.exit(1)

    print(f"Video: {original_filename} ({storage_path}), fps={fps}")
    print(
        f"PRESSURE_SCORE_REFERENCE_PX={settings.PRESSURE_SCORE_REFERENCE_PX} "
        f"weights(pressure/congestion/bottleneck/reverse_flow)="
        f"{settings.RISK_SCORE_WEIGHT_PRESSURE}/{settings.RISK_SCORE_WEIGHT_CONGESTION}/"
        f"{settings.RISK_SCORE_WEIGHT_BOTTLENECK}/{settings.RISK_SCORE_WEIGHT_REVERSE_FLOW}"
    )
    print(
        f"PREDICTIVE_WINDOW_SECONDS={settings.PREDICTIVE_WINDOW_SECONDS} "
        f"PREDICTION_HORIZON_SECONDS={settings.PREDICTION_HORIZON_SECONDS}"
    )
    print(PIXEL_UNITS_NOTE)
    print("Constructing YOLO11nDetector, ByteTrackAdapter, DISOpticalFlowAdapter...")
    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()

    grid = CrowdGrid.from_frame_dimensions(video.width, video.height)
    bottleneck_detector = BottleneckDetector(grid)
    reverse_flow_detector = ReverseFlowDetector(grid)
    pressure_projector = PressureProjector()

    frames_processed = 0

    # Timed as THIS PHASE'S incremental computation ONLY (risk score +
    # predictive projection) — everything upstream (detection/tracking/
    # optical-flow, Phase 9's density/flow/pressure, Phase 10's congestion/
    # bottleneck/reverse_flow) is already measured in its own prior preview
    # scripts, so this isolates just what Phase 11 adds.
    phase11_elapsed = 0.0

    risk_scores = []
    confidences = []
    full_signal_frame_count = 0

    prev_frame = None
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if frames_processed >= CONTIGUOUS_FRAME_COUNT:
                break

            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds

                # Phases 9-10 (untimed here — already measured in their own
                # preview scripts).
                core = compute_core_crowd_metrics(
                    tracking_result, motion_result, frame.width, frame.height, elapsed_seconds
                )
                congestion = compute_congestion_field(core.density, core.flow)
                bottleneck = bottleneck_detector.update(core.flow)
                reverse_flow = reverse_flow_detector.update(core.flow)

                t0 = time.perf_counter()
                risk_result = compute_risk_score(
                    core.density, core.pressure, congestion, bottleneck, reverse_flow
                )
                projection = pressure_projector.update(core.pressure)
                phase11_elapsed += time.perf_counter() - t0

                risk_scores.append(risk_result.risk_score)
                confidences.append(risk_result.confidence)
                if len(risk_result.contributing_signals) == 4:
                    full_signal_frame_count += 1

                projection_display = (
                    "n/a (window filling)"
                    if projection is None
                    else f"projected_pressure={projection.projected_pressure:.3f} r_squared={projection.r_squared:.3f}"
                )

                print(
                    f"  frame {frame.frame_number}: risk_score={risk_result.risk_score:.2f} "
                    f"confidence={risk_result.confidence:.2f} "
                    f"contributing_signals={risk_result.contributing_signals} "
                    f"{projection_display}"
                )

            frames_processed += 1
            prev_frame = frame

    pairs_processed = frames_processed - 1
    fps_measured = pairs_processed / phase11_elapsed if phase11_elapsed > 0 else 0.0

    print()
    print("=== Summary ===")
    print(PIXEL_UNITS_NOTE)
    print(f"Frames processed:                          {frames_processed}")
    print(f"Frame-pairs computed:                      {pairs_processed}")
    if risk_scores:
        print(f"Average risk_score:                        {sum(risk_scores) / len(risk_scores):.3f}")
        print(f"Min / Max risk_score observed:             {min(risk_scores):.3f} / {max(risk_scores):.3f}")
        print(f"Average confidence:                        {sum(confidences) / len(confidences):.3f}")
    print(
        f"Frames with full 4/4 contributing signals: {full_signal_frame_count} / {pairs_processed}"
    )
    print(f"Phase-11-only elapsed time:                 {phase11_elapsed:.3f}s")
    print(f"Phase-11-only FPS (measured):                {fps_measured:.3f}")


if __name__ == "__main__":
    main()
