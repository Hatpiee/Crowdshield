"""Debug CLI (Acute-Hazard Trigger Phase): chains the full real pipeline
(FrameSource -> YOLO11nDetector -> ByteTrackAdapter -> DISOpticalFlowAdapter
-> CrowdMetricsEngine -> AcuteHazardDetector) against a real video,
continuously, every frame, printing each frame's raw candidate-signal
values and z-scores — this is the calibration tool the module docstrings
of acute_hazard_detector.py / config.py's ACUTE_HAZARD_* settings point to.

============================================================
STATUS (see DECISIONS.md — read before trusting any output from this script
against a "blast" video)
============================================================
The developer's own described regression video
(`rapidsave.com_-j0jrk8iqypc61.mp4`) was found, on direct inspection
(matching SHA256 hash), to be BYTE-IDENTICAL to `people_clip.mp4` — the
real blast footage has not yet been supplied to this repo. Running this
script against that video_id today produces CALM-BASELINE data only, not
genuine event data — this script has NOT yet been run against real blast
footage, and the ACUTE_HAZARD_* threshold defaults in config.py are
correspondingly still marked UNVALIDATED ENGINEERING JUDGMENT. Once real
blast footage is supplied, re-run this script against it and compare the
printed percentiles here against that run's — pick thresholds from the
real observed gap between the two distributions, exactly as
RISK_ELEVATED_THRESHOLD/RISK_STATE_FALL_HYSTERESIS_MARGIN were originally
calibrated (see risk_state.py's config comments).

Usage: python scripts/preview_acute_hazard_signals.py <video_id>
"""

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


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_acute_hazard_signals.py <video_id>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])
    db = SessionLocal()

    video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
    if video is None:
        print(f"No video found with id={video_id}")
        sys.exit(1)
    storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
    fps, frame_width, frame_height = video.fps, video.width, video.height
    if not storage_path.exists() or not fps or fps <= 0 or not frame_width or not frame_height:
        print("Video file/metadata missing — cannot process.")
        sys.exit(1)

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

    raw_history: dict[str, list[float]] = {name: [] for name in SIGNAL_ORDER}
    fired_frames = []

    print()
    print("=== per-frame raw signal values (every frame, continuous) ===")
    prev_frame = None
    frame_count = 0
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
                for name in SIGNAL_ORDER:
                    raw_history[name].append(signal.raw_values[name])

                marker = ""
                if signal.is_acute_hazard:
                    fired_frames.append(signal)
                    marker = "  <== ACUTE_HAZARD"
                if frame_count % 10 == 0 or signal.is_acute_hazard:
                    print(
                        f"  frame {frame.frame_number} t={frame.timestamp_seconds:.2f}s "
                        f"motion={signal.raw_values['motion_energy']:.2f} "
                        f"div={signal.raw_values['flow_divergence']:.4f} "
                        f"det_delta={signal.raw_values['detection_count_delta']:.0f} "
                        f"scene={signal.raw_values['scene_change']:.4f} "
                        f"z={ {k: round(v, 1) for k, v in signal.z_scores.items()} }{marker}"
                    )

            frame_count += 1
            prev_frame = frame

    print()
    print("=== Real-measured raw-value distributions (per signal) ===")
    for name in SIGNAL_ORDER:
        values = np.array(raw_history[name])
        if values.size == 0:
            continue
        print(
            f"  {name}: p25={np.percentile(values, 25):.4f} median={np.median(values):.4f} "
            f"p75={np.percentile(values, 75):.4f} p90={np.percentile(values, 90):.4f} "
            f"p95={np.percentile(values, 95):.4f} max={values.max():.4f} mean={values.mean():.4f}"
        )

    print()
    print(f"ACUTE_HAZARD fired on {len(fired_frames)} of {frame_count - 1} frame-pairs.")
    if not fired_frames:
        print(
            "No firings — plausible/expected on calm footage (see this script's "
            "own module docstring re: real blast footage not yet supplied)."
        )

    db.close()


if __name__ == "__main__":
    main()
