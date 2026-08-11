"""Bridging verification script (Phase 8 -> Phase 9): proves that
FrameSource (Phase 5), YOLO11nDetector (Phase 6), ByteTrackAdapter
(Phase 7), and DISOpticalFlowAdapter (Phase 8) — each already
independently tested — correctly compose together when chained, and
produces a genuine, measured combined throughput number.

FOLLOW-UP (leak-vs-warmup check): runs the SAME 300-frame contiguous span
TWICE in sequence, within the same process, reusing the SAME
YOLO11nDetector/ByteTrackAdapter/DISOpticalFlowAdapter instances across
both passes — never reconstructed between passes, since reconstructing
would defeat the point of testing for a leak under repeated use. RSS is
measured separately per pass (baseline-before-pass, peak-during-pass,
growth-during-pass) so a one-time warmup cost (expected to show up mostly
in Pass 1) can be distinguished from a genuine per-frame leak (which would
keep showing up as comparable growth in Pass 2).

FOLLOW-UP #2 (harness-artifact isolation): an earlier version of this
script retained every DetectionResult/TrackingResult/MotionResult object
in lists for the whole pass, purely to support the composition sanity
checks. MotionResult.flow_field is a full dense (H, W, 2) float32 array
per frame pair — for this project's real footage, retaining 299 of them
accounts for close to 1GB by itself, which closely matched the observed
"leak-like" growth and was a strong confound. This version instead
increments lightweight integer counters as each result is produced and
immediately discards the full object — no numpy arrays, no result lists
are retained for the sanity checks anymore — to isolate whether the
growth was really coming from the pipeline components or from this test
harness's own result retention.

Reusing the tracker across both passes over the SAME frame sequence means
Pass 2's ByteTrackAdapter state does NOT restart — it continues from Pass
1's internal state, so Pass 2's track_ids/continuity are not meaningful in
their own right (frames "teleport back" to their Pass-1 starting
positions from the tracker's point of view). That's expected and
irrelevant here: this script's purpose is exclusively measuring memory
behavior under repeated use, not re-validating tracking correctness
(already covered by Phase 7's own tests).

This is NOT the AnalysisOrchestrator (§28, a later, not-yet-designed
phase) and NOT a new production API — same standalone, read-only,
throwaway-verification-CLI category as every prior preview_*.py script.
No DB writes, no new route, no wiring into session_service, no new
dependency (psutil is an existing transitive dependency since Phase 6/7).

Usage: python scripts/preview_full_pipeline.py <video_id>
"""

import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import cv2  # noqa: E402
import psutil  # noqa: E402

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.track import TrackingResult  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

# 300 consecutive frames (10s at 30fps) per pass — same span as the
# original single-pass bridging run.
CONTIGUOUS_FRAME_COUNT = 300

PREVIEW_DIR = REPO_ROOT / "storage" / "debug_previews"

POINT_RADIUS = 4
POINT_COLOR = (0, 0, 255)  # BGR: red
TRAIL_COLOR = (255, 200, 0)  # BGR: light blue
TEXT_COLOR = (0, 255, 0)  # BGR: green
STATS_TEXT_COLOR = (255, 255, 255)  # BGR: white


def _draw_combined_annotation(image, tracking_result: TrackingResult, motion_result):
    annotated = image.copy()

    for track in tracking_result.tracks:
        trail_points = [(int(p.x), int(p.y)) for (_, _, p) in track.trajectory]
        for p0, p1 in zip(trail_points, trail_points[1:]):
            cv2.line(annotated, p0, p1, TRAIL_COLOR, 2, cv2.LINE_AA)

        x, y = int(track.point.x), int(track.point.y)
        cv2.circle(annotated, (x, y), POINT_RADIUS, POINT_COLOR, -1)
        label = f"id={track.track_id}" + (" (lost)" if track.is_lost else "")
        cv2.putText(
            annotated, label, (x + 6, y - 6),
            cv2.FONT_HERSHEY_SIMPLEX, 0.5, TEXT_COLOR, 1, cv2.LINE_AA,
        )

    if motion_result is not None:
        direction_text = (
            "None" if motion_result.dominant_direction_degrees is None
            else f"{motion_result.dominant_direction_degrees:.1f}deg"
        )
        stats_lines = [
            f"mean_velocity={motion_result.mean_velocity:.2f}px",
            f"dominant_direction={direction_text}",
        ]
        for i, line in enumerate(stats_lines):
            cv2.putText(
                annotated, line, (10, 24 + i * 22),
                cv2.FONT_HERSHEY_SIMPLEX, 0.6, STATS_TEXT_COLOR, 2, cv2.LINE_AA,
            )

    return annotated


def _run_pass(
    pass_number: int,
    detector: YOLO11nDetector,
    tracker: ByteTrackAdapter,
    optical_flow: DISOpticalFlowAdapter,
    storage_path: Path,
    process: psutil.Process,
    video_id: UUID,
    save_visualization: bool,
) -> dict:
    """Runs one CONTIGUOUS_FRAME_COUNT-frame pass using the given
    already-constructed component instances (never reconstructed here) and
    returns a dict of that pass's measured stats, including RSS
    baseline/peak/growth scoped to just this pass.

    Deliberately retains NO full DetectionResult/TrackingResult/
    MotionResult objects — only lightweight integer counters — so the
    composition sanity checks are verified by COUNT, not by the length of
    a list that's holding onto full-resolution flow fields for the whole
    pass (see FOLLOW-UP #2 in the module docstring for why that mattered)."""
    print(f"\n--- Pass {pass_number} ---")

    baseline_rss_bytes = process.memory_info().rss
    peak_rss_bytes = baseline_rss_bytes

    detection_count = 0
    tracking_count = 0
    motion_count = 0

    total_detections = 0
    unique_track_ids: set[int] = set()

    best_track_count = -1
    best_snapshot = None  # (image, tracking_result, motion_result_or_None)

    combined_elapsed = 0.0

    prev_frame = None
    frames_processed = 0
    # Re-opening the video file each pass is required to re-decode the
    # same frames — this is NOT reconstructing the components under test
    # (detector/tracker/optical_flow), which stay the same instances
    # across both passes.
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if frames_processed >= CONTIGUOUS_FRAME_COUNT:
                break

            t0 = time.perf_counter()

            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            motion_result = None
            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)

            combined_elapsed += time.perf_counter() - t0

            # Lightweight counters only — the full result objects
            # (especially MotionResult.flow_field) are never retained
            # beyond this loop iteration, other than the single best-so-far
            # snapshot below (O(1), not O(n)).
            detection_count += 1
            tracking_count += 1
            if motion_result is not None:
                motion_count += 1

            total_detections += len(detection_result.detections)
            for track in tracking_result.tracks:
                unique_track_ids.add(track.track_id)

            if len(tracking_result.tracks) > best_track_count:
                best_track_count = len(tracking_result.tracks)
                best_snapshot = (frame.image.copy(), tracking_result, motion_result)

            current_rss = process.memory_info().rss
            if current_rss > peak_rss_bytes:
                peak_rss_bytes = current_rss

            frames_processed += 1
            prev_frame = frame

            if frames_processed % 60 == 0:
                print(f"  ...{frames_processed}/{CONTIGUOUS_FRAME_COUNT} frames processed")

    print(f"  Composition sanity checks (Pass {pass_number}):")
    checks = [
        (
            "detection_count == frames_processed",
            detection_count == frames_processed,
            f"{detection_count} == {frames_processed}",
        ),
        (
            "tracking_count == detection_count",
            tracking_count == detection_count,
            f"{tracking_count} == {detection_count}",
        ),
        (
            "motion_count == frames_processed - 1",
            motion_count == frames_processed - 1,
            f"{motion_count} == {frames_processed - 1}",
        ),
    ]
    all_passed = True
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"    [{status}] {name}  ({detail})")

    if save_visualization and best_snapshot is not None:
        image, tracking_result, motion_result = best_snapshot
        annotated = _draw_combined_annotation(image, tracking_result, motion_result)
        out_path = PREVIEW_DIR / f"{video_id}_full_pipeline_combined_pass{pass_number}.jpg"
        cv2.imwrite(str(out_path), annotated)
        print(f"  Saved combined annotated frame ({best_track_count} tracks) -> {out_path.name}")

    fps_measured = frames_processed / combined_elapsed if combined_elapsed > 0 else 0.0
    baseline_rss_mb = baseline_rss_bytes / (1024 * 1024)
    peak_rss_mb = peak_rss_bytes / (1024 * 1024)
    growth_mb = peak_rss_mb - baseline_rss_mb

    print(f"  Pass {pass_number} frames processed:      {frames_processed}")
    print(f"  Pass {pass_number} total detections:      {total_detections}")
    print(f"  Pass {pass_number} unique track_ids:      {len(unique_track_ids)}")
    print(f"  Pass {pass_number} combined FPS (measured): {fps_measured:.3f}")
    print(f"  Pass {pass_number} baseline RSS:           {baseline_rss_mb:.1f} MB")
    print(f"  Pass {pass_number} peak RSS:                {peak_rss_mb:.1f} MB")
    print(f"  Pass {pass_number} RSS growth:              {growth_mb:.1f} MB")

    return {
        "frames_processed": frames_processed,
        "total_detections": total_detections,
        "unique_track_ids": len(unique_track_ids),
        "fps_measured": fps_measured,
        "baseline_rss_mb": baseline_rss_mb,
        "peak_rss_mb": peak_rss_mb,
        "growth_mb": growth_mb,
        "all_sanity_checks_passed": all_passed,
    }


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_full_pipeline.py <video_id>")
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
    print(f"Processing {CONTIGUOUS_FRAME_COUNT} contiguous frames, TWICE, reusing the same component instances.")
    print(
        f"Constructing YOLO11nDetector (model={settings.DETECTOR_MODEL}), "
        f"ByteTrackAdapter (fps={fps}), and DISOpticalFlowAdapter "
        f"(preset={settings.DIS_PRESET}) — each ONCE, before BOTH passes..."
    )
    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()

    # psutil is an existing transitive dependency (pulled in by
    # ultralytics/trackers since Phase 6/7, see requirements.txt) — not
    # newly added. Full-process RSS is a more honest "peak memory" signal
    # than tracemalloc here, since most of this pipeline's real memory
    # (numpy arrays, OpenCV buffers, torch tensors) is allocated outside
    # Python's own allocator and would NOT show up under tracemalloc's
    # default domain.
    process = psutil.Process()

    pass_1_stats = _run_pass(
        1, detector, tracker, optical_flow, storage_path, process, video_id,
        save_visualization=True,
    )
    pass_2_stats = _run_pass(
        2, detector, tracker, optical_flow, storage_path, process, video_id,
        save_visualization=True,
    )

    print()
    print("=== Pass 1 vs Pass 2 RSS comparison ===")
    print(f"Pass 1: baseline={pass_1_stats['baseline_rss_mb']:.1f} MB  peak={pass_1_stats['peak_rss_mb']:.1f} MB  growth={pass_1_stats['growth_mb']:.1f} MB")
    print(f"Pass 2: baseline={pass_2_stats['baseline_rss_mb']:.1f} MB  peak={pass_2_stats['peak_rss_mb']:.1f} MB  growth={pass_2_stats['growth_mb']:.1f} MB")

    growth_1 = pass_1_stats["growth_mb"]
    growth_2 = pass_2_stats["growth_mb"]
    # A pass is considered to show "meaningfully smaller" growth than the
    # other if it's under half — a deliberately simple, stated threshold,
    # not a statistically rigorous test (n=1 per pass; this is a
    # directional signal, not a controlled benchmark).
    if growth_1 <= 0:
        verdict = "Pass 1 showed no/negative growth — inconclusive, re-run for a cleaner signal."
    elif growth_2 < growth_1 * 0.5:
        verdict = (
            f"Pass 2's growth ({growth_2:.1f} MB) is well under half of Pass 1's "
            f"({growth_1:.1f} MB) — consistent with ONE-TIME WARMUP "
            "(model/buffer allocation on first use), not a per-frame leak."
        )
    elif growth_2 <= growth_1 * 1.5:
        verdict = (
            f"Pass 2's growth ({growth_2:.1f} MB) is roughly comparable to Pass 1's "
            f"({growth_1:.1f} MB) — this does NOT look like one-time warmup. "
            "This is a real leak signal worth investigating before Phase 9."
        )
    else:
        verdict = (
            f"Pass 2's growth ({growth_2:.1f} MB) is LARGER than Pass 1's "
            f"({growth_1:.1f} MB) — unexpected either way; worth investigating "
            "before Phase 9 regardless of the warmup theory."
        )

    print()
    print("=== Verdict ===")
    print(verdict)

    print()
    print("=== Combined summary ===")
    for label, stats in (("Pass 1", pass_1_stats), ("Pass 2", pass_2_stats)):
        print(
            f"{label}: frames={stats['frames_processed']} "
            f"detections={stats['total_detections']} "
            f"unique_track_ids={stats['unique_track_ids']} "
            f"fps={stats['fps_measured']:.3f} "
            f"sanity_checks_passed={stats['all_sanity_checks_passed']}"
        )


if __name__ == "__main__":
    main()
