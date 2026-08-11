"""Debug CLI: run YOLO11nDetector on a sample of frames from an already
uploaded video and save annotated preview images for visual sanity
checking. Not part of the running application and not reachable via any
HTTP route (Phase 6 explicitly adds no detection API route) — same
standalone-CLI pattern as scripts/create_admin.py (Phase 2).

Usage: python scripts/preview_detection.py <video_id>
"""

import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

# Sample every 30th frame rather than processing the whole video — on
# CPU-only YOLO11n inference, decoding+detecting every frame of a
# multi-minute clip would take far longer than necessary for a visual
# sanity check, while every-30th-frame still samples across the full
# duration of the clip at a manageable, bounded frame count.
FRAME_SAMPLE_STEP = 30

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"

POINT_RADIUS = 4
POINT_COLOR = (0, 0, 255)  # BGR: red
TEXT_COLOR = (0, 255, 0)  # BGR: green


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_detection.py <video_id>")
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
        f"Loading YOLO11nDetector (model={settings.DETECTOR_MODEL}, "
        f"conf={settings.DETECTOR_CONFIDENCE_THRESHOLD})..."
    )
    detector = YOLO11nDetector()

    frames_processed = 0
    total_detections = 0

    # Timed separately from decode/draw/save so the reported FPS reflects
    # detection-only work, per §33's "measured, never claimed" requirement.
    detect_elapsed = 0.0

    with MP4FrameSource(storage_path, frame_step=FRAME_SAMPLE_STEP) as source:
        for frame in source.frames():
            t0 = time.perf_counter()
            result = detector.detect(frame)
            detect_elapsed += time.perf_counter() - t0

            frames_processed += 1
            total_detections += len(result.detections)

            annotated = frame.image.copy()
            for detection in result.detections:
                x, y = int(detection.point.x), int(detection.point.y)
                cv2.circle(annotated, (x, y), POINT_RADIUS, POINT_COLOR, -1)
                cv2.putText(
                    annotated,
                    f"{detection.confidence:.2f}",
                    (x + 6, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    TEXT_COLOR,
                    1,
                    cv2.LINE_AA,
                )

            out_path = PREVIEW_DIR / f"{video_id}_frame{frame.frame_number:06d}.jpg"
            cv2.imwrite(str(out_path), annotated)
            print(
                f"  frame {frame.frame_number}: {len(result.detections)} "
                f"detection(s) -> {out_path.name}"
            )

    avg_detections = (
        total_detections / frames_processed if frames_processed > 0 else 0.0
    )
    fps = frames_processed / detect_elapsed if detect_elapsed > 0 else 0.0

    print()
    print("=== Summary ===")
    print(f"Frames processed:              {frames_processed}")
    print(f"Total detections:              {total_detections}")
    print(f"Average detections per frame:  {avg_detections:.2f}")
    print(f"Detection-only elapsed time:   {detect_elapsed:.3f}s")
    print(f"Detection-only FPS (measured): {fps:.3f}")


if __name__ == "__main__":
    main()
