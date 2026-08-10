from pathlib import Path

import cv2
import numpy as np

# Small and fast on purpose — this is a per-test fixture, not a real video.
DEFAULT_WIDTH = 64
DEFAULT_HEIGHT = 64
DEFAULT_FPS = 10.0
DEFAULT_NUM_FRAMES = 10


def generate_synthetic_mp4(
    path: Path,
    num_frames: int = DEFAULT_NUM_FRAMES,
    width: int = DEFAULT_WIDTH,
    height: int = DEFAULT_HEIGHT,
    fps: float = DEFAULT_FPS,
) -> None:
    """Writes a genuinely OpenCV-decodable MP4 to `path` via cv2.VideoWriter
    directly — no ffmpeg install or external test-video download required,
    so the test suite stays fully self-contained.

    Each frame is a distinct solid color (intensity = frame_number * 20 % 256)
    so frames are visually distinguishable, but test assertions should rely
    on MP4FrameSource's own frame_number counter rather than exact decoded
    pixel values — mp4v is a lossy codec, so pixel-perfect round-tripping
    isn't guaranteed.
    """
    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
    writer = cv2.VideoWriter(str(path), fourcc, fps, (width, height))
    if not writer.isOpened():
        raise RuntimeError(f"Failed to open VideoWriter for {path}")
    try:
        for i in range(num_frames):
            frame = np.full(
                (height, width, 3), fill_value=(i * 20) % 256, dtype=np.uint8
            )
            writer.write(frame)
    finally:
        writer.release()
