"""Bridging verification script (Phase 11 -> Phase 12), same category and
methodology as the Phase 8->9 bridging check (scripts/preview_full_pipeline.py).
That script proved FrameSource/YOLO11nDetector/ByteTrackAdapter/
DISOpticalFlowAdapter compose correctly and don't leak under repeated use.
This script extends the SAME two-pass, lightweight-retention methodology to
the three NEW stateful accumulators completed in Phase 11 — BottleneckDetector,
ReverseFlowDetector, PressureProjector (layered on top of Phase 7's Tracker,
which the original script already covers) — chaining the ENTIRE pipeline
through full CrowdMetrics (Phases 9/10/11) via CrowdMetricsEngine.

METHODOLOGY (identical rationale to the Phase 8 script — see its own
docstring for the full history of why this specific approach was adopted):
- A LONGER contiguous span than any prior preview script (600 frames vs the
  previous longest of 300 — this video has 603 frames total at 30fps/20.1s,
  so 600 is effectively "the whole video").
- Runs ONCE, then AGAIN in the same process, reusing the SAME already-
  constructed detector/tracker/optical_flow/CrowdMetricsEngine instances
  (never reconstructed between passes) — this is what actually exercises
  "repeated use" memory behavior, not just "processes N frames once."
- RSS measured separately per pass (baseline-before-pass, peak-during-pass,
  growth-during-pass) so one-time warmup (expected mostly in Pass 1) can be
  distinguished from a genuine per-frame leak (which would keep showing up
  as comparable growth in Pass 2).
- LIGHTWEIGHT RETENTION ONLY: no full DetectionResult/TrackingResult/
  MotionResult/CrowdMetrics objects (or their internal numpy grids/flow
  fields) are retained across the whole pass — only small scalar counters
  and a single O(1) best-snapshot for the saved visualization. This is the
  exact discipline the Phase 8 script's own FOLLOW-UP #2 established was
  necessary after an earlier version's full-object retention was itself
  mistaken for a pipeline leak.

REPLAY CAVEAT (same one already documented in DECISIONS.md's
"Implementation-Discovered Constraints" section for ByteTrackAdapter,
extended here to the Phase 11 accumulators): reusing stateful components
across two passes over the SAME frame sequence means Pass 2's timestamps
restart from ~0s while each component's internal state still reflects
Pass 1 ending at ~20s. This is explicitly OUT-OF-CONTRACT usage (a single
instance must process exactly one continuous, monotonically-increasing
pass through one video) — this script deliberately does it anyway, on
purpose, specifically to see how each accumulator behaves under that
out-of-contract replay, the same way the original Phase 8 script did for
ByteTrackAdapter. Findings from this replay scenario are reported honestly
below, whatever they are — including if a component does NOT gracefully
handle it, per this task's explicit instruction not to fix or hide such a
finding.

This is NOT the AnalysisOrchestrator (§28, a later phase) and NOT a new
production API — same standalone, read-only, throwaway-verification-CLI
category as every prior preview_*.py script. No DB writes, no new route, no
wiring into session_service, no new dependency (psutil is an existing
transitive dependency since Phase 6/7).

Usage: python scripts/preview_full_crowd_intelligence_bridging.py <video_id>
"""

import sys
import time
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import psutil  # noqa: E402

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402

# 600 contiguous frames — this video has 603 total (30fps, 20.1s), so this
# is effectively the WHOLE video, and comfortably exceeds every prior
# preview script's span (the previous longest was 300, in the Phase 8
# bridging check).
CONTIGUOUS_FRAME_COUNT = 600

# How often (in frames) to sample ReverseFlowDetector's/PressureProjector's
# internal storage sizes — sampling every frame is unnecessary (the whole
# point is these should be CONSTANT/bounded; a coarse sample is enough to
# prove that) and keeps the sample list itself lightweight.
STATE_SIZE_SAMPLE_INTERVAL = 60


def _reverse_flow_state_sizes(reverse_flow_detector) -> tuple[int, int, int]:
    """Reads ReverseFlowDetector's PRIVATE internal state directly (same
    debug-introspection pattern this project has used before, e.g. Phase 7's
    investigation of ByteTrackTracker.tracks) to verify its per-cell storage
    is O(cell count), not O(frame count):
      - baseline_vector.size:  should be rows*cols*2, fixed forever.
      - observation_count.size: should be rows*cols, fixed forever.
      - len(persistence_history): should be bounded by
        REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES (a deque maxlen), NOT by how
        many frames have been processed so far — if this grew unbounded, it
        would mean an accidental per-frame history list instead of a true
        bounded EMA/ring-buffer per cell.
    """
    baseline_size = reverse_flow_detector._baseline_vector.size
    observation_size = reverse_flow_detector._observation_count.size
    persistence_len = len(reverse_flow_detector._persistence_history)
    return baseline_size, observation_size, persistence_len


def _run_pass(
    pass_number: int,
    detector: YOLO11nDetector,
    tracker: ByteTrackAdapter,
    optical_flow: DISOpticalFlowAdapter,
    engine: CrowdMetricsEngine,
    storage_path: Path,
    process: psutil.Process,
) -> dict:
    print(f"\n--- Pass {pass_number} ---")

    baseline_rss_bytes = process.memory_info().rss
    peak_rss_bytes = baseline_rss_bytes

    detection_count = 0
    tracking_count = 0
    motion_count = 0
    crowd_metrics_count = 0
    bottleneck_available_count = 0
    projection_available_count = 0
    full_signal_count = 0

    total_detections = 0
    unique_track_ids: set[int] = set()

    # Lightweight scalars only, per frame — never the full CrowdMetrics
    # object (which holds several numpy grids per component).
    risk_scores: list[float] = []
    bottleneck_window_sizes: list[int] = []  # window_frames_used when available
    projector_data_points: list[int] = []  # data_points_used when available

    # (frame_number, baseline_size, observation_size, persistence_len)
    # sampled every STATE_SIZE_SAMPLE_INTERVAL frames — a handful of small
    # tuples for the whole pass, not one per frame.
    reverse_flow_state_samples: list[tuple[int, int, int]] = []

    combined_elapsed = 0.0

    prev_frame = None
    frames_processed = 0
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if frames_processed >= CONTIGUOUS_FRAME_COUNT:
                break

            t0 = time.perf_counter()

            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds

                crowd_metrics = engine.update(tracking_result, motion_result, elapsed_seconds)

                motion_count += 1
                crowd_metrics_count += 1
                risk_scores.append(crowd_metrics.risk_score.risk_score)
                if len(crowd_metrics.risk_score.contributing_signals) == 4:
                    full_signal_count += 1
                if crowd_metrics.bottleneck is not None:
                    bottleneck_available_count += 1
                    bottleneck_window_sizes.append(crowd_metrics.bottleneck.window_frames_used)
                if crowd_metrics.predictive_projection is not None:
                    projection_available_count += 1
                    projector_data_points.append(
                        crowd_metrics.predictive_projection.data_points_used
                    )

            combined_elapsed += time.perf_counter() - t0

            detection_count += 1
            tracking_count += 1
            total_detections += len(detection_result.detections)
            for track in tracking_result.tracks:
                unique_track_ids.add(track.track_id)

            if frames_processed % STATE_SIZE_SAMPLE_INTERVAL == 0:
                reverse_flow_state_samples.append(
                    (frames_processed, *_reverse_flow_state_sizes(engine._reverse_flow_detector))
                )

            current_rss = process.memory_info().rss
            if current_rss > peak_rss_bytes:
                peak_rss_bytes = current_rss

            frames_processed += 1
            prev_frame = frame

            if frames_processed % 100 == 0:
                print(f"  ...{frames_processed}/{CONTIGUOUS_FRAME_COUNT} frames processed")

    # Final sample, so the pass's very last state is captured too.
    reverse_flow_state_samples.append(
        (frames_processed, *_reverse_flow_state_sizes(engine._reverse_flow_detector))
    )

    grid = engine._reverse_flow_detector._grid
    expected_cell_count = grid.rows * grid.cols

    print(f"  ReverseFlowDetector per-cell state samples (Pass {pass_number}):")
    print(f"    Expected constant size: {expected_cell_count} cells "
          f"(baseline_vector={expected_cell_count * 2} floats, "
          f"observation_count={expected_cell_count} ints)")
    for frame_num, baseline_size, observation_size, persistence_len in reverse_flow_state_samples:
        print(
            f"    frame {frame_num}: baseline_vector.size={baseline_size} "
            f"observation_count.size={observation_size} "
            f"persistence_history_len={persistence_len}"
        )
    baseline_sizes = {s[1] for s in reverse_flow_state_samples}
    observation_sizes = {s[2] for s in reverse_flow_state_samples}
    persistence_lens = {s[3] for s in reverse_flow_state_samples}
    reverse_flow_state_constant = (
        baseline_sizes == {expected_cell_count * 2}
        and observation_sizes == {expected_cell_count}
        and max(persistence_lens) <= settings.REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES
    )
    print(
        f"    [{'PASS' if reverse_flow_state_constant else 'FAIL'}] "
        f"per-cell state size constant across the pass "
        f"(baseline_vector.size always {expected_cell_count * 2}, "
        f"observation_count.size always {expected_cell_count}, "
        f"persistence_history_len never exceeds "
        f"{settings.REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES})"
    )

    print(f"  PressureProjector window-size behavior (Pass {pass_number}):")
    if projector_data_points:
        print(
            f"    data_points_used: min={min(projector_data_points)} "
            f"max={max(projector_data_points)} "
            f"(configured PREDICTIVE_WINDOW_SECONDS={settings.PREDICTIVE_WINDOW_SECONDS}s; "
            "under normal monotonic single-pass use this should stay bounded to "
            "roughly that many seconds' worth of frames — see the final "
            "'Verdict: PressureProjector window bound' section for the actual "
            "expected-frame-count comparison)"
        )
    else:
        print("    No projections available this pass (window never filled).")

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
        (
            "crowd_metrics_count == motion_count",
            crowd_metrics_count == motion_count,
            f"{crowd_metrics_count} == {motion_count}",
        ),
        (
            "bottleneck_available_count <= crowd_metrics_count",
            bottleneck_available_count <= crowd_metrics_count,
            f"{bottleneck_available_count} <= {crowd_metrics_count}",
        ),
        (
            "projection_available_count <= crowd_metrics_count",
            projection_available_count <= crowd_metrics_count,
            f"{projection_available_count} <= {crowd_metrics_count}",
        ),
        (
            "every risk_score in [0, 100]",
            all(0.0 <= r <= 100.0 for r in risk_scores),
            f"min={min(risk_scores):.2f} max={max(risk_scores):.2f}" if risk_scores else "n/a",
        ),
    ]
    all_passed = True
    for name, passed, detail in checks:
        status = "PASS" if passed else "FAIL"
        if not passed:
            all_passed = False
        print(f"    [{status}] {name}  ({detail})")

    fps_measured = frames_processed / combined_elapsed if combined_elapsed > 0 else 0.0
    baseline_rss_mb = baseline_rss_bytes / (1024 * 1024)
    peak_rss_mb = peak_rss_bytes / (1024 * 1024)
    growth_mb = peak_rss_mb - baseline_rss_mb

    print(f"  Pass {pass_number} frames processed:         {frames_processed}")
    print(f"  Pass {pass_number} total detections:         {total_detections}")
    print(f"  Pass {pass_number} unique track_ids:         {len(unique_track_ids)}")
    print(f"  Pass {pass_number} frames with 4/4 signals:  {full_signal_count} / {crowd_metrics_count}")
    print(f"  Pass {pass_number} combined FPS (measured):  {fps_measured:.3f}")
    print(f"  Pass {pass_number} baseline RSS:              {baseline_rss_mb:.1f} MB")
    print(f"  Pass {pass_number} peak RSS:                   {peak_rss_mb:.1f} MB")
    print(f"  Pass {pass_number} RSS growth:                 {growth_mb:.1f} MB")

    return {
        "frames_processed": frames_processed,
        "total_detections": total_detections,
        "unique_track_ids": len(unique_track_ids),
        "fps_measured": fps_measured,
        "baseline_rss_mb": baseline_rss_mb,
        "peak_rss_mb": peak_rss_mb,
        "growth_mb": growth_mb,
        "all_sanity_checks_passed": all_passed,
        "reverse_flow_state_constant": reverse_flow_state_constant,
        "projector_data_points_max": max(projector_data_points) if projector_data_points else None,
        "expected_cell_count": expected_cell_count,
    }


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_full_crowd_intelligence_bridging.py <video_id>")
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
        frame_width = video.width
        frame_height = video.height
        frame_count = video.frame_count
    finally:
        db.close()

    if not storage_path.exists():
        print(f"Video file not found on disk: {storage_path}")
        sys.exit(1)
    if not fps or fps <= 0:
        print(f"Video has no valid stored fps ({fps!r}) — cannot construct a tracker.")
        sys.exit(1)

    print(
        f"Video: {original_filename} ({storage_path}), fps={fps}, "
        f"{frame_width}x{frame_height}, {frame_count} frames total"
    )
    print(
        f"Processing {CONTIGUOUS_FRAME_COUNT} contiguous frames, TWICE, "
        "reusing the same component instances (including CrowdMetricsEngine's "
        "internal BottleneckDetector/ReverseFlowDetector/PressureProjector)."
    )
    print(
        f"Constructing YOLO11nDetector, ByteTrackAdapter (fps={fps}), "
        f"DISOpticalFlowAdapter, and CrowdMetricsEngine "
        f"({frame_width}x{frame_height}) — each ONCE, before BOTH passes..."
    )
    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)

    # psutil is an existing transitive dependency (see requirements.txt) —
    # not newly added. Full-process RSS, same rationale as the Phase 8
    # bridging script: most of this pipeline's real memory (numpy arrays,
    # OpenCV buffers, torch tensors) is allocated outside Python's own
    # allocator and would not show up under tracemalloc's default domain.
    process = psutil.Process()

    pass_1_stats = _run_pass(1, detector, tracker, optical_flow, engine, storage_path, process)
    pass_2_stats = _run_pass(2, detector, tracker, optical_flow, engine, storage_path, process)

    print()
    print("=== Pass 1 vs Pass 2 RSS comparison ===")
    print(
        f"Pass 1: baseline={pass_1_stats['baseline_rss_mb']:.1f} MB  "
        f"peak={pass_1_stats['peak_rss_mb']:.1f} MB  growth={pass_1_stats['growth_mb']:.1f} MB"
    )
    print(
        f"Pass 2: baseline={pass_2_stats['baseline_rss_mb']:.1f} MB  "
        f"peak={pass_2_stats['peak_rss_mb']:.1f} MB  growth={pass_2_stats['growth_mb']:.1f} MB"
    )

    growth_1 = pass_1_stats["growth_mb"]
    growth_2 = pass_2_stats["growth_mb"]
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
            "This is a real leak signal worth investigating before Phase 12."
        )
    else:
        verdict = (
            f"Pass 2's growth ({growth_2:.1f} MB) is LARGER than Pass 1's "
            f"({growth_1:.1f} MB) — unexpected either way; worth investigating "
            "before Phase 12 regardless of the warmup theory."
        )

    print()
    print("=== Verdict: RSS growth ===")
    print(verdict)

    print()
    print("=== Verdict: ReverseFlowDetector per-cell state ===")
    for label, stats in (("Pass 1", pass_1_stats), ("Pass 2", pass_2_stats)):
        status = "CONSTANT (O(cell count), not O(frame count))" if stats["reverse_flow_state_constant"] else "GREW — INVESTIGATE"
        print(f"{label}: {status}")

    print()
    print("=== Verdict: PressureProjector window bound ===")
    expected_frames_per_window = int(settings.PREDICTIVE_WINDOW_SECONDS * fps)
    for label, stats in (("Pass 1", pass_1_stats), ("Pass 2", pass_2_stats)):
        max_points = stats["projector_data_points_max"]
        if max_points is None:
            print(f"{label}: no projections produced.")
        elif max_points <= expected_frames_per_window * 1.2:
            print(
                f"{label}: max data_points_used={max_points}, within the expected "
                f"~{expected_frames_per_window}-frame window (PREDICTIVE_WINDOW_SECONDS="
                f"{settings.PREDICTIVE_WINDOW_SECONDS}s @ {fps}fps) — bounded as intended."
            )
        else:
            print(
                f"{label}: max data_points_used={max_points}, EXCEEDS the expected "
                f"~{expected_frames_per_window}-frame window by more than 20% — the "
                "time-based rolling window did not prune as expected. See report for "
                "root cause (this is the timestamp-reset replay caveat, not a genuine "
                "unbounded leak — see module docstring)."
            )

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
