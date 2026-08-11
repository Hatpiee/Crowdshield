"""Debug CLI: run DISOpticalFlowAdapter on a contiguous span of frames from
an already uploaded video and save the standard HSV-encoded flow
visualization. Not part of the running application and not reachable via
any HTTP route — same standalone-CLI pattern as the prior preview scripts.

Optical flow requires true frame-to-frame adjacency (same lesson as Phase
7's tracking script), so this processes a CONTIGUOUS span (frame_step=1),
not sparse sampling.

Usage: python scripts/preview_optical_flow.py <video_id>
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
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402

# 150 consecutive frames (5s at 30fps) — same span as Phase 7's tracking
# preview, long enough to be informative without an excessive runtime.
CONTIGUOUS_FRAME_COUNT = 150

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"


def _hsv_flow_visualization(flow_field: np.ndarray) -> np.ndarray:
    """Standard dense-optical-flow HSV visualization: hue = direction,
    value = normalized magnitude, full saturation, converted to BGR for
    saving. Established technique, not invented here."""
    magnitude, angle_degrees = cv2.cartToPolar(
        flow_field[..., 0], flow_field[..., 1], angleInDegrees=True
    )
    height, width = flow_field.shape[:2]
    hsv = np.zeros((height, width, 3), dtype=np.uint8)
    hsv[..., 0] = (angle_degrees / 2).astype(np.uint8)  # OpenCV hue range is 0-179
    hsv[..., 1] = 255
    hsv[..., 2] = cv2.normalize(magnitude, None, 0, 255, cv2.NORM_MINMAX).astype(np.uint8)
    return cv2.cvtColor(hsv, cv2.COLOR_HSV2BGR)


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_optical_flow.py <video_id>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])

    db = SessionLocal()
    try:
        video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
        if video is None:
            print(f"No video found with id={video_id}")
            sys.exit(1)
        storage_path = (
            REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
        )
        original_filename = video.original_filename
    finally:
        db.close()

    if not storage_path.exists():
        print(f"Video file not found on disk: {storage_path}")
        sys.exit(1)

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    print(f"Video: {original_filename} ({storage_path})")
    print(
        f"Constructing DISOpticalFlowAdapter (preset={settings.DIS_PRESET}, "
        f"noise_floor={settings.MOTION_MAGNITUDE_NOISE_FLOOR})..."
    )
    optical_flow = DISOpticalFlowAdapter()

    pairs_processed = 0
    mean_velocity_sum = 0.0

    # Timed as optical-flow-ONLY (not combined with detection this time) —
    # a cleaner number to compare against §5's own cited "~21-48 FPS
    # depending on preset" range.
    flow_elapsed = 0.0

    prev_frame = None
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if prev_frame is not None:
                if pairs_processed >= CONTIGUOUS_FRAME_COUNT:
                    break

                t0 = time.perf_counter()
                result = optical_flow.compute(prev_frame, frame)
                flow_elapsed += time.perf_counter() - t0

                pairs_processed += 1
                mean_velocity_sum += result.mean_velocity

                vis = _hsv_flow_visualization(result.flow_field)
                out_path = (
                    PREVIEW_DIR
                    / f"{video_id}_flow_frame{result.prev_frame_number:06d}_{result.frame_number:06d}.jpg"
                )
                cv2.imwrite(str(out_path), vis)

                print(
                    f"  frames {result.prev_frame_number}->{result.frame_number}: "
                    f"mean_velocity={result.mean_velocity:.3f} "
                    f"velocity_variance={result.velocity_variance:.3f} "
                    f"dominant_direction_degrees="
                    f"{'None' if result.dominant_direction_degrees is None else f'{result.dominant_direction_degrees:.1f}'} "
                    f"directional_entropy="
                    f"{'None' if result.directional_entropy is None else f'{result.directional_entropy:.3f}'} "
                    f"-> {out_path.name}"
                )

            prev_frame = frame

    avg_mean_velocity = mean_velocity_sum / pairs_processed if pairs_processed > 0 else 0.0
    fps_measured = pairs_processed / flow_elapsed if flow_elapsed > 0 else 0.0

    print()
    print("=== Summary ===")
    print(f"Frame pairs processed:              {pairs_processed}")
    print(f"Average mean_velocity:               {avg_mean_velocity:.3f} px/frame-interval")
    print(f"Optical-flow-only elapsed time:      {flow_elapsed:.3f}s")
    print(f"Optical-flow-only FPS (measured):    {fps_measured:.3f}")


if __name__ == "__main__":
    main()
