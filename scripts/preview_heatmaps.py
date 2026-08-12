"""Debug CLI (Phase 12): chains the FULL pipeline through Phase 11
(FrameSource -> YOLO11nDetector -> ByteTrackAdapter -> DISOpticalFlowAdapter
-> CrowdMetricsEngine) against people_clip.mp4, and for a SAMPLED subset of
frames, calls Phase 12's generate_and_persist_heatmaps.

======================================================================
GENUINE REAL PERSISTENCE — UNLIKE EVERY PRIOR PREVIEW SCRIPT SINCE PHASE 6
======================================================================
Every preview_*.py script since Phase 6 has been pure read-only/throwaway:
it reads an already-uploaded video and prints/saves debug visualizations to
storage/debug_previews/ (gitignored, not part of the real application data
model) — never writing to the real database or the real application
storage tree. THIS script is different, deliberately: it creates a real
AnalysisSession row (via Phase 4's session_service.create_session — the
first real exercise of that function outside its own Phase 4 tests) and
writes real HeatmapSnapshot rows plus real JPEG files under
storage/heatmaps/ — the actual persisted artifacts this phase's API routes
serve metadata for. This is intentional, one-time verification persistence
for THIS specific script, not a new general pattern for preview scripts —
every other preview_*.py script remains read-only.

======================================================================
CADENCE IS DEMONSTRATION-ONLY — NOT A PRODUCTION CLAIM
======================================================================
This script calls generate_and_persist_heatmaps every HEATMAP_SAMPLE_INTERVAL
frames purely so this demo doesn't write hundreds of heatmap sets to disk
for a single test run. The full crowd-intelligence pipeline (tracker/
bottleneck/reverse-flow/pressure-projector state) still runs on EVERY frame
as normal — only the decision of WHEN to persist heatmaps is sampled here.
The not-yet-built AnalysisOrchestrator (§28) is what will actually decide
the real production cadence (every frame? every N seconds? on risk-state
change?) — this script's sampling interval is NOT that decision and must
not be read as a claim about it.

Usage: python scripts/preview_heatmaps.py <video_id>
"""

import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.heatmap import HeatmapSnapshot, HeatmapType  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.heatmap_rendering import render_density_heatmap, render_pressure_heatmap  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import heatmap_service, session_service  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150
# DEMONSTRATION cadence only — see module docstring. Every 20th frame keeps
# this run to ~7 generation events (~35 files) instead of ~150 sets.
HEATMAP_SAMPLE_INTERVAL = 20

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_heatmaps.py <video_id>")
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

    PREVIEW_DIR.mkdir(parents=True, exist_ok=True)

    print(
        f"Video: {video.original_filename} ({storage_path}), fps={fps}, "
        f"{frame_width}x{frame_height}"
    )
    print(
        f"Processing {CONTIGUOUS_FRAME_COUNT} contiguous frames; generating "
        f"heatmaps every {HEATMAP_SAMPLE_INTERVAL} frames "
        "(DEMONSTRATION cadence only — see module docstring, NOT a "
        "production-cadence claim)."
    )
    print(f"HEATMAP_STORAGE_PATH={settings.HEATMAP_STORAGE_PATH}")

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)

    generated_counts = {heatmap_type: 0 for heatmap_type in HeatmapType}
    skip_log: list[tuple[int, str, str]] = []  # (frame_number, type, reason)
    frames_sampled = 0

    # The debug composite is saved for the BEST-populated sampled frame
    # (highest track_count), not simply the first one — an early frame can
    # legitimately have zero confirmed tracks yet (ByteTrack's own
    # confirmation lag, established since Phase 7), which would make for an
    # uninformative, near-uniform composite that doesn't actually show
    # whether heatmap colors align with real people.
    best_track_count = -1
    best_snapshot = None  # (frame_image, density_image, pressure_image)

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

                if frames_processed % HEATMAP_SAMPLE_INTERVAL == 0:
                    frames_sampled += 1
                    result = heatmap_service.generate_and_persist_heatmaps(
                        db, session.id, frame.frame_number, frame.timestamp_seconds,
                        crowd_metrics, frame_width, frame_height,
                    )
                    for heatmap_type in result.generated:
                        generated_counts[heatmap_type] += 1
                    for heatmap_type, reason in result.skipped.items():
                        skip_log.append((frame.frame_number, heatmap_type.value, reason))

                    print(
                        f"  frame {frame.frame_number}: generated="
                        f"{[t.value for t in result.generated]} skipped="
                        f"{[t.value for t in result.skipped]}"
                    )

                    track_count = len(tracking_result.tracks)
                    if (
                        HeatmapType.DENSITY in result.generated
                        and HeatmapType.PRESSURE in result.generated
                        and track_count > best_track_count
                    ):
                        best_track_count = track_count
                        density_image = render_density_heatmap(
                            crowd_metrics.core.density, frame_width, frame_height
                        )
                        pressure_image = render_pressure_heatmap(
                            crowd_metrics.core.pressure, frame_width, frame_height
                        )
                        best_snapshot = (frame.frame_number, frame.image.copy(), density_image, pressure_image)

            frames_processed += 1
            prev_frame = frame

    if best_snapshot is not None:
        best_frame_number, frame_image, density_image, pressure_image = best_snapshot
        density_composite = cv2.addWeighted(frame_image, 0.5, density_image, 0.5, 0)
        pressure_composite = cv2.addWeighted(frame_image, 0.5, pressure_image, 0.5, 0)
        density_path = PREVIEW_DIR / f"{session.id}_density_composite.jpg"
        pressure_path = PREVIEW_DIR / f"{session.id}_pressure_composite.jpg"
        cv2.imwrite(str(density_path), density_composite)
        cv2.imwrite(str(pressure_path), pressure_composite)
        print(
            f"Saved DEBUG-ONLY composite visualizations (best-populated sampled "
            f"frame {best_frame_number}, {best_track_count} tracks) -> "
            f"storage/debug_previews/{density_path.name}, {pressure_path.name}"
        )

    print()
    print("=== Generation summary ===")
    print(f"Frames processed (full pipeline, every frame): {frames_processed}")
    print(f"Frames sampled for heatmap generation: {frames_sampled}")
    for heatmap_type in HeatmapType:
        print(f"  {heatmap_type.value}: generated {generated_counts[heatmap_type]}/{frames_sampled}")
    if skip_log:
        print("Skips:")
        for frame_number, type_value, reason in skip_log:
            print(f"  {type_value} skipped at frame {frame_number}: {reason}")
    else:
        print("No skips.")

    print()
    print("=== Round-trip verification (direct service-layer queries, same as the API routes) ===")
    all_rows = (
        db.query(HeatmapSnapshot)
        .filter(HeatmapSnapshot.session_id == session.id)
        .order_by(HeatmapSnapshot.created_at.desc())
        .all()
    )
    print(f"Total HeatmapSnapshot rows for session {session.id}: {len(all_rows)}")

    storage_dir = heatmap_service.get_storage_dir()
    for heatmap_type in HeatmapType:
        latest = (
            db.query(HeatmapSnapshot)
            .filter(
                HeatmapSnapshot.session_id == session.id,
                HeatmapSnapshot.heatmap_type == heatmap_type,
            )
            .order_by(HeatmapSnapshot.created_at.desc())
            .first()
        )
        if latest is None:
            print(f"  {heatmap_type.value}: no snapshot found (equivalent to API 404)")
            continue
        file_path = storage_dir / latest.file_path
        exists = file_path.exists()
        real_size = file_path.stat().st_size if exists else None
        print(
            f"  {heatmap_type.value}: latest frame={latest.frame_number} "
            f"file_path={latest.file_path} db_file_size_bytes={latest.file_size_bytes} "
            f"real_file_exists={exists} real_file_size_bytes={real_size} "
            f"sizes_match={real_size == latest.file_size_bytes}"
        )

    db.close()


if __name__ == "__main__":
    main()
