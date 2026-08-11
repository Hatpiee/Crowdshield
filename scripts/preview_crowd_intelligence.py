"""Debug CLI: run FrameSource + YOLO11nDetector + ByteTrackAdapter +
DISOpticalFlowAdapter + this phase's Density/Flow/Pressure computation on a
contiguous span of frames from an already uploaded video, and save a basic
sanity-check visualization. Not part of the running application and not
reachable via any HTTP route — same standalone-CLI pattern as every prior
preview_*.py script.

======================================================================
PIXEL-SPACE UNITS ONLY — READ THIS BEFORE READING ANY NUMBER BELOW
======================================================================
Every density/velocity/pressure value this script prints is in PIXEL-based
units (density in people/cell, velocity in pixels/second, pressure in
people * pixels^2/second^2 per cell) — NOT the literature's meter-based
units. This project has no camera calibration or homography step. The
0.02/0.04 s^-2 literature thresholds for Crowd Pressure are NOT directly
comparable to the pressure values below. See DECISIONS.md's "Known
Structural Limitation: Pixel-Space vs. Real-World Units" section.
======================================================================

The visualization here is a DELIBERATELY BASIC OpenCV colormap overlay for
sanity-checking this phase's output — it is NOT the mandatory 5-heatmap-
type system, which is an explicitly separate, later phase with its own
requirements.

Usage: python scripts/preview_crowd_intelligence.py <video_id>
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
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.core_crowd_metrics import compute_core_crowd_metrics  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

# 150 consecutive frames (5s at 30fps) — same span as Phase 7/8's own
# preview scripts.
CONTIGUOUS_FRAME_COUNT = 150

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"

PIXEL_UNITS_DISCLAIMER = (
    "[PIXEL-SPACE UNITS, NOT METERS — not directly comparable to "
    "literature thresholds without camera calibration; see DECISIONS.md]"
)


def _colormap_overlay(frame_image: np.ndarray, grid: np.ndarray) -> np.ndarray:
    """Deliberately basic: normalize the grid to 0-255, upscale to frame
    size with nearest-neighbor (so cell boundaries stay visible, matching
    what was actually computed rather than implying false sub-cell
    precision), apply OpenCV's JET colormap, alpha-blend over the frame."""
    grid_max = grid.max()
    normalized = (grid / grid_max * 255.0) if grid_max > 0 else grid
    normalized_u8 = normalized.astype(np.uint8)

    height, width = frame_image.shape[:2]
    upscaled = cv2.resize(normalized_u8, (width, height), interpolation=cv2.INTER_NEAREST)
    colored = cv2.applyColorMap(upscaled, cv2.COLORMAP_JET)

    return cv2.addWeighted(frame_image, 0.5, colored, 0.5, 0)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_crowd_intelligence.py <video_id>")
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
    print(PIXEL_UNITS_DISCLAIMER)
    print("Constructing YOLO11nDetector, ByteTrackAdapter, DISOpticalFlowAdapter...")
    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()

    frames_processed = 0
    confidence_sum = 0.0

    # Timed as crowd-intelligence-ONLY (density+flow+pressure) — detection/
    # tracking/optical-flow are already measured in their own prior
    # preview scripts, so this isolates just this phase's new cost.
    crowd_intelligence_elapsed = 0.0

    best_track_count = -1
    best_snapshot = None  # (frame_image, density_grid, pressure_grid)

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

                t0 = time.perf_counter()
                metrics = compute_core_crowd_metrics(
                    tracking_result, motion_result, frame.width, frame.height, elapsed_seconds
                )
                crowd_intelligence_elapsed += time.perf_counter() - t0

                confidence_sum += metrics.density.estimation_confidence

                print(
                    f"  frame {frame.frame_number}: track_count={metrics.density.track_count} "
                    f"max_density={metrics.density.grid.max():.3f} "
                    f"mean_density={metrics.density.grid.mean():.3f} "
                    f"max_pressure={metrics.pressure.max_pressure:.3f} "
                    f"mean_pressure={metrics.pressure.mean_pressure:.3f} "
                    f"confidence={metrics.density.estimation_confidence:.2f} "
                    f"degradation={metrics.density.degradation_reason} "
                    f"{PIXEL_UNITS_DISCLAIMER}"
                )

                track_count = len(tracking_result.tracks)
                if track_count > best_track_count:
                    best_track_count = track_count
                    best_snapshot = (
                        frame.image.copy(),
                        metrics.density.grid.copy(),
                        metrics.pressure.grid.copy(),
                    )

            frames_processed += 1
            prev_frame = frame

    if best_snapshot is not None:
        frame_image, density_grid, pressure_grid = best_snapshot

        density_vis = _colormap_overlay(frame_image, density_grid)
        density_path = PREVIEW_DIR / f"{video_id}_crowd_density_overlay.jpg"
        cv2.imwrite(str(density_path), density_vis)

        pressure_vis = _colormap_overlay(frame_image, pressure_grid)
        pressure_path = PREVIEW_DIR / f"{video_id}_crowd_pressure_overlay.jpg"
        cv2.imwrite(str(pressure_path), pressure_vis)

        print(
            f"\nSaved density overlay ({best_track_count} tracks) -> {density_path.name}"
        )
        print(f"Saved pressure overlay ({best_track_count} tracks) -> {pressure_path.name}")

    # frames_processed - 1 crowd-metrics computations ran (first frame has
    # no prev_frame to pair with).
    pairs_processed = frames_processed - 1
    avg_confidence = confidence_sum / pairs_processed if pairs_processed > 0 else 0.0
    fps_measured = (
        pairs_processed / crowd_intelligence_elapsed if crowd_intelligence_elapsed > 0 else 0.0
    )

    print()
    print("=== Summary ===")
    print(PIXEL_UNITS_DISCLAIMER)
    print(f"Frames processed:                       {frames_processed}")
    print(f"Crowd-metrics computations:              {pairs_processed}")
    print(f"Average estimation_confidence:           {avg_confidence:.3f}")
    print(f"Crowd-intelligence-only elapsed time:    {crowd_intelligence_elapsed:.3f}s")
    print(f"Crowd-intelligence-only FPS (measured):  {fps_measured:.3f}")


if __name__ == "__main__":
    main()
