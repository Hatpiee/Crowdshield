"""Debug CLI: run YOLO11nDetector + ByteTrackAdapter on a contiguous span
of frames from an already uploaded video and save annotated preview
images. Not part of the running application and not reachable via any
HTTP route — same standalone-CLI pattern as scripts/create_admin.py and
scripts/preview_detection.py (Phase 6).

IMPORTANT DIFFERENCE from Phase 6's preview_detection.py: tracking needs
temporal continuity, so this script processes a CONTIGUOUS span of
consecutive frames (frame_step=1) rather than sparse sampling — sparse
sampling would break tracking continuity entirely.

Usage: python scripts/preview_tracking.py <video_id>
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
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

# 150 consecutive frames (5s at 30fps) — long enough to show sustained
# tracking continuity (ids persisting, trails forming) without an
# excessive CPU-only runtime; not the whole video by default.
CONTIGUOUS_FRAME_COUNT = 150

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"

POINT_RADIUS = 4
POINT_COLOR = (0, 0, 255)  # BGR: red
TRAIL_COLOR = (255, 200, 0)  # BGR: light blue
TEXT_COLOR = (0, 255, 0)  # BGR: green


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_tracking.py <video_id>")
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
    print(
        f"Loading YOLO11nDetector (model={settings.DETECTOR_MODEL}, "
        f"conf={settings.DETECTOR_CONFIDENCE_THRESHOLD})..."
    )
    detector = YOLO11nDetector()

    # Constructed ONCE before the frame loop — a Tracker is stateful and
    # must never be reused across videos, but within a single video's
    # processing it must be the same instance across every frame.
    tracker = ByteTrackAdapter(fps=fps)

    frames_processed = 0
    unique_track_ids: set[int] = set()
    total_tracks = 0

    # Timed separately from decode/draw/save, per §33 — genuine
    # detection+tracking-only elapsed time.
    processing_elapsed = 0.0

    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if frames_processed >= CONTIGUOUS_FRAME_COUNT:
                break

            t0 = time.perf_counter()
            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)
            processing_elapsed += time.perf_counter() - t0

            frames_processed += 1
            total_tracks += len(tracking_result.tracks)
            for track in tracking_result.tracks:
                unique_track_ids.add(track.track_id)

            annotated = frame.image.copy()
            for track in tracking_result.tracks:
                trail_points = [
                    (int(p.x), int(p.y)) for (_, _, p) in track.trajectory
                ]
                for p0, p1 in zip(trail_points, trail_points[1:]):
                    cv2.line(annotated, p0, p1, TRAIL_COLOR, 2, cv2.LINE_AA)

                x, y = int(track.point.x), int(track.point.y)
                cv2.circle(annotated, (x, y), POINT_RADIUS, POINT_COLOR, -1)
                label = f"id={track.track_id}" + (" (lost)" if track.is_lost else "")
                cv2.putText(
                    annotated,
                    label,
                    (x + 6, y - 6),
                    cv2.FONT_HERSHEY_SIMPLEX,
                    0.5,
                    TEXT_COLOR,
                    1,
                    cv2.LINE_AA,
                )

            out_path = PREVIEW_DIR / f"{video_id}_track_frame{frame.frame_number:06d}.jpg"
            cv2.imwrite(str(out_path), annotated)
            print(
                f"  frame {frame.frame_number}: {len(tracking_result.tracks)} "
                f"track(s) -> {out_path.name}"
            )

    avg_tracks = total_tracks / frames_processed if frames_processed > 0 else 0.0
    fps_measured = frames_processed / processing_elapsed if processing_elapsed > 0 else 0.0

    print()
    print("=== Summary ===")
    print(f"Frames processed:                    {frames_processed}")
    print(f"Unique track_ids seen:                {len(unique_track_ids)}")
    print(f"Average tracks per frame:             {avg_tracks:.2f}")
    print(f"Detection+tracking elapsed time:      {processing_elapsed:.3f}s")
    print(f"Detection+tracking FPS (measured):    {fps_measured:.3f}")


if __name__ == "__main__":
    main()
