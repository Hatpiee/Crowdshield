"""Debug CLI: chain FrameSource + YOLO11nDetector + ByteTrackAdapter +
DISOpticalFlowAdapter + Phase 9's Density/Flow/Pressure + this phase's
Congestion/Bottleneck/Reverse Flow on a contiguous span of frames from an
already uploaded video, and save a basic sanity-check visualization. Not
part of the running application and not reachable via any HTTP route —
same standalone-CLI pattern as every prior preview_*.py script.

======================================================================
PIXEL-SPACE UNITS — READ THIS BEFORE READING ANY NUMBER BELOW
======================================================================
Density/velocity/pressure numbers are in PIXEL-based units (see Phase 9's
own units disclaimer, carried forward in DECISIONS.md). This phase's
DENSITY_CONGESTION_THRESHOLD and FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC
are ALSO pixel-space-native and uncalibrated against any real venue.
Reverse Flow's angle-based deviation threshold is UNIT-AGNOSTIC and does
NOT have this units problem — noted explicitly so the disclosure isn't
over-applied where it doesn't belong.
======================================================================

The congestion visualization here is a DELIBERATELY BASIC OpenCV colormap
overlay for sanity-checking this phase's output — it is NOT the mandatory
5-heatmap-type system, which is an explicitly separate, later phase with
its own requirements.

Usage: python scripts/preview_congestion_bottleneck_reverse.py <video_id>
"""

import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import numpy as np  # noqa: E402

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
from app.pipeline.reverse_flow import ReverseFlowDetector  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"

PIXEL_UNITS_NOTE = (
    "[Congestion thresholds are PIXEL-SPACE, NOT METERS-calibrated; "
    "Reverse Flow's angle threshold is unit-agnostic — see DECISIONS.md]"
)


def _colormap_overlay(frame_image: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Same deliberately-basic pattern as Phase 9's preview script."""
    grid_max = grid.max()
    normalized = (grid / grid_max * 255.0) if grid_max > 0 else grid
    normalized_u8 = normalized.astype(np.uint8)

    height, width = frame_image.shape[:2]
    upscaled = cv2.resize(normalized_u8, (width, height), interpolation=cv2.INTER_NEAREST)
    colored = cv2.applyColorMap(upscaled, cv2.COLORMAP_JET)

    return cv2.addWeighted(frame_image, 0.5, colored, 0.5, 0)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_congestion_bottleneck_reverse.py <video_id>")
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

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Video: {original_filename} ({storage_path}), fps={fps}")
    print(f"Grid cell size: {settings.CROWD_GRID_CELL_SIZE_PX}px")
    print(
        f"DENSITY_CONGESTION_THRESHOLD={settings.DENSITY_CONGESTION_THRESHOLD} "
        f"FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC="
        f"{settings.FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC}"
    )
    print(
        f"BOTTLENECK_WINDOW_FRAMES={settings.BOTTLENECK_WINDOW_FRAMES} "
        f"REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS={settings.REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS} "
        f"REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES={settings.REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES} "
        f"REVERSE_FLOW_PERSISTENCE_MIN_COUNT={settings.REVERSE_FLOW_PERSISTENCE_MIN_COUNT}"
    )
    print(PIXEL_UNITS_NOTE)
    print("Constructing YOLO11nDetector, ByteTrackAdapter, DISOpticalFlowAdapter...")
    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()

    # BottleneckDetector/ReverseFlowDetector are STATEFUL — constructed once
    # here, per video/session, and updated incrementally below. Grid is
    # deterministic from frame dimensions + settings, known from the video's
    # stored metadata, so it can be built up front (same pattern as
    # ByteTrackAdapter needing fps up front).
    grid = CrowdGrid.from_frame_dimensions(video.width, video.height)
    bottleneck_detector = BottleneckDetector(grid)
    reverse_flow_detector = ReverseFlowDetector(grid)

    frames_processed = 0

    # Timed as THIS PHASE'S incremental computation ONLY (congestion +
    # bottleneck + reverse_flow) — detection/tracking/optical-flow and
    # Phase 9's density/flow/pressure are already measured in their own
    # prior preview scripts, so this isolates just what Phase 10 adds.
    phase10_elapsed = 0.0

    frames_with_any_congestion = 0
    any_bottleneck_ever = False
    strongest_bottleneck_score = None  # (score, frame_number, cell)
    any_reverse_flow_ever = False
    reverse_flow_events = []  # (frame_number, cell, fraction)

    best_track_count = -1
    best_snapshot = None  # (frame_image, congestion_score_grid)

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

                # Phase 9 (untimed here — already measured in
                # preview_crowd_intelligence.py).
                core = compute_core_crowd_metrics(
                    tracking_result, motion_result, frame.width, frame.height, elapsed_seconds
                )

                t0 = time.perf_counter()
                congestion = compute_congestion_field(core.density, core.flow)
                bottleneck = bottleneck_detector.update(core.flow)
                reverse_flow = reverse_flow_detector.update(core.flow)
                phase10_elapsed += time.perf_counter() - t0

                if congestion.congested_cell_fraction > 0:
                    frames_with_any_congestion += 1

                if bottleneck is not None:
                    finite_scores = bottleneck.bottleneck_score_grid[
                        ~np.isnan(bottleneck.bottleneck_score_grid)
                    ]
                    if finite_scores.size > 0 and finite_scores.min() < 1.0:
                        any_bottleneck_ever = True
                    if finite_scores.size > 0:
                        min_score = float(finite_scores.min())
                        if strongest_bottleneck_score is None or min_score < strongest_bottleneck_score[0]:
                            strongest_bottleneck_score = (
                                min_score,
                                frame.frame_number,
                                bottleneck.strongest_bottleneck_cell,
                            )

                if reverse_flow.reverse_flow_cell_fraction > 0:
                    any_reverse_flow_ever = True
                    reversed_cells = list(zip(*np.where(reverse_flow.is_reverse_flow_grid)))
                    reverse_flow_events.append(
                        (frame.frame_number, reversed_cells, reverse_flow.reverse_flow_cell_fraction)
                    )

                if bottleneck is None:
                    bottleneck_display = "n/a (window filling)"
                else:
                    finite = bottleneck.bottleneck_score_grid[
                        ~np.isnan(bottleneck.bottleneck_score_grid)
                    ]
                    bottleneck_display = f"{finite.min():.3f}" if finite.size > 0 else "nan"

                print(
                    f"  frame {frame.frame_number}: track_count={core.density.track_count} "
                    f"congested_cell_fraction={congestion.congested_cell_fraction:.3f} "
                    f"bottleneck_min_score={bottleneck_display} "
                    f"reverse_flow_cell_fraction={reverse_flow.reverse_flow_cell_fraction:.3f} "
                    f"established_baseline_cells={reverse_flow.cells_with_established_baseline}"
                )

                track_count = len(tracking_result.tracks)
                if track_count > best_track_count:
                    best_track_count = track_count
                    best_snapshot = (frame.image.copy(), congestion.congestion_score_grid.copy())

            frames_processed += 1
            prev_frame = frame

    if best_snapshot is not None:
        frame_image, congestion_score_grid = best_snapshot
        congestion_vis = _colormap_overlay(frame_image, congestion_score_grid)
        congestion_path = PREVIEW_DIR / f"{video_id}_congestion_overlay.jpg"
        cv2.imwrite(str(congestion_path), congestion_vis)
        print(f"\nSaved congestion overlay ({best_track_count} tracks) -> {congestion_path.name}")

    pairs_processed = frames_processed - 1
    fps_measured = pairs_processed / phase10_elapsed if phase10_elapsed > 0 else 0.0

    print()
    print("=== Summary ===")
    print(PIXEL_UNITS_NOTE)
    print(f"Frames processed:                          {frames_processed}")
    print(f"Frame-pairs computed:                      {pairs_processed}")
    print(f"Frames with any congested cell:             {frames_with_any_congestion}")
    print(f"Any bottleneck signal ever detected:        {any_bottleneck_ever}")
    if strongest_bottleneck_score is not None:
        score, fnum, cell = strongest_bottleneck_score
        print(
            f"  Strongest observed bottleneck_score:      {score:.3f} at frame {fnum}, cell {cell}"
        )
    print(f"Any reverse-flow signal ever detected:      {any_reverse_flow_ever}")
    if reverse_flow_events:
        for fnum, cells, fraction in reverse_flow_events[:10]:
            print(f"  frame {fnum}: cells={cells} fraction={fraction:.3f}")
        if len(reverse_flow_events) > 10:
            print(f"  ... and {len(reverse_flow_events) - 10} more frame(s) with reverse flow")
    print(f"Phase-10-only elapsed time:                 {phase10_elapsed:.3f}s")
    print(f"Phase-10-only FPS (measured):                {fps_measured:.3f}")


if __name__ == "__main__":
    main()
