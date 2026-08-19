"""Diagnostic (Acute-Hazard Precision phase), Step 1/2: instruments the REAL
`AcuteHazardDetector` against a real video, capturing a full per-frame
machine-readable calibration record — not just the trigger frames, and not
just a printed summary (extends scripts/preview_acute_hazard_signals.py's
own real-run precedent with richer per-frame diagnostics needed to analyze
the false-positive signature: persistence across neighboring frames, and a
cheap deterministic spatial-coherence measure derived from the already-
computed `localization_grid`, reusing existing grid data rather than
building a new CV subsystem).

For EVERY frame-pair: all 4 raw signal values, all 4 z-scores, which
signals individually fired, whether quorum was met, is_acute_hazard, and
two spatial-coherence diagnostics computed from localization_grid (the
per-cell |flow_divergence| grid already computed for ROI selection):
  - active_cell_fraction: fraction of cells exceeding (per-frame mean +
    2*std) of that SAME frame's own grid — a purely relative, per-frame
    "how much of the grid lit up" measure.
  - largest_component_fraction: largest 4-connected component among the
    active cells, as a fraction of all active cells — 1.0 means the
    active cells form ONE coherent blob; a low value means they are
    scattered/noisy.

Every real ACUTE_HAZARD trigger gets an additional printed block showing
its own signal values plus the SAME signals 2 frames before and 2 frames
after, to assess temporal persistence (a real distinguishing feature
between a genuine event and a single-frame noise spike).

Output: a JSON file (all per-frame records + all trigger contexts) for
offline analysis, plus a human-readable summary printed to stdout.

Usage: python scripts/calibrate_acute_hazard_false_positives.py <video_id> <output_json_path>
"""

import json
import sys
from pathlib import Path
from uuid import UUID

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.acute_hazard_detector import AcuteHazardDetector, SIGNAL_ORDER  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402


def _spatial_coherence(localization_grid: np.ndarray) -> tuple[float, float, int]:
    """Returns (active_cell_fraction, largest_component_fraction, num_active_cells)."""
    grid = localization_grid.astype(np.float64)
    mean = grid.mean()
    std = grid.std()
    threshold = mean + 2.0 * std
    active_mask = grid > threshold
    num_active = int(active_mask.sum())
    total_cells = grid.size
    active_fraction = num_active / total_cells if total_cells > 0 else 0.0

    if num_active == 0:
        return active_fraction, 0.0, num_active

    # Simple 4-connected component labeling over the small grid (rows x cols,
    # e.g. ~6x8) via a manual flood fill — no new CV dependency needed for a
    # grid this small.
    rows, cols = active_mask.shape
    visited = np.zeros_like(active_mask, dtype=bool)
    largest = 0
    for r in range(rows):
        for c in range(cols):
            if active_mask[r, c] and not visited[r, c]:
                stack = [(r, c)]
                visited[r, c] = True
                size = 0
                while stack:
                    cr, cc = stack.pop()
                    size += 1
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = cr + dr, cc + dc
                        if 0 <= nr < rows and 0 <= nc < cols and active_mask[nr, nc] and not visited[nr, nc]:
                            visited[nr, nc] = True
                            stack.append((nr, nc))
                largest = max(largest, size)
    largest_fraction = largest / num_active
    return active_fraction, largest_fraction, num_active


def main() -> None:
    if len(sys.argv) != 3:
        print("Usage: python scripts/calibrate_acute_hazard_false_positives.py <video_id> <output_json_path>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])
    output_path = Path(sys.argv[2])
    db = SessionLocal()

    video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
    if video is None:
        print(f"No video found with id={video_id}")
        sys.exit(1)
    storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
    fps, frame_width, frame_height = video.fps, video.width, video.height

    print(f"Video: {video.original_filename} ({storage_path}), fps={fps}, {frame_width}x{frame_height}")
    print(
        f"Active thresholds: ZSCORE={settings.ACUTE_HAZARD_ZSCORE_THRESHOLD} "
        f"MIN_CORROBORATING={settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS} "
        f"MIN_BASELINE_OBS={settings.ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS} "
        f"EMA_ALPHA={settings.ACUTE_HAZARD_BASELINE_EMA_ALPHA} "
        f"COOLDOWN_S={settings.ACUTE_HAZARD_COOLDOWN_SECONDS}"
    )

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)
    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
    acute_detector = AcuteHazardDetector(grid)

    records: list[dict] = []
    prev_frame = None
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = engine.update(tracking_result, motion_result, elapsed_seconds)

                signal = acute_detector.update(
                    motion_result, crowd_metrics.core.flow, detection_result, prev_frame, frame
                )
                active_frac, largest_frac, num_active = _spatial_coherence(signal.localization_grid)

                records.append({
                    "frame_number": signal.frame_number,
                    "timestamp_seconds": signal.timestamp_seconds,
                    "raw_values": signal.raw_values,
                    "z_scores": signal.z_scores,
                    "corroborating_signals": signal.corroborating_signals,
                    "baseline_established": signal.baseline_established,
                    "is_acute_hazard": signal.is_acute_hazard,
                    "spatial_active_cell_fraction": active_frac,
                    "spatial_largest_component_fraction": largest_frac,
                    "spatial_num_active_cells": num_active,
                })

            prev_frame = frame

    trigger_indices = [i for i, r in enumerate(records) if r["is_acute_hazard"]]
    print()
    print(f"=== {len(trigger_indices)} real ACUTE_HAZARD trigger(s) in {len(records)} frame-pairs ===")
    trigger_contexts = []
    for idx in trigger_indices:
        context = records[max(0, idx - 2): idx + 3]
        trigger_contexts.append({"trigger_index": idx, "context": context})
        print(f"\n--- Trigger at frame {records[idx]['frame_number']} t={records[idx]['timestamp_seconds']:.2f}s ---")
        for r in context:
            marker = "  <== TRIGGER" if r["is_acute_hazard"] else ""
            print(
                f"  t={r['timestamp_seconds']:.2f}s motion_z={r['z_scores']['motion_energy']:.2f} "
                f"div_z={r['z_scores']['flow_divergence']:.2f} det_z={r['z_scores']['detection_count_delta']:.2f} "
                f"scene_z={r['z_scores']['scene_change']:.2f} corroborating={r['corroborating_signals']} "
                f"spatial_active_frac={r['spatial_active_cell_fraction']:.3f} "
                f"largest_component_frac={r['spatial_largest_component_fraction']:.3f}{marker}"
            )

    output = {
        "video_id": str(video_id),
        "video_filename": video.original_filename,
        "fps": fps,
        "signal_order": list(SIGNAL_ORDER),
        "config": {
            "ACUTE_HAZARD_ZSCORE_THRESHOLD": settings.ACUTE_HAZARD_ZSCORE_THRESHOLD,
            "ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS": settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS,
            "ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS": settings.ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS,
        },
        "records": records,
        "trigger_contexts": trigger_contexts,
    }
    output_path.parent.mkdir(parents=True, exist_ok=True)
    output_path.write_text(json.dumps(output, indent=2))
    print(f"\nWrote full calibration artifact to {output_path}")

    db.close()


if __name__ == "__main__":
    main()
