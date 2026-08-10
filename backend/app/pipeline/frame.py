from dataclasses import dataclass

import numpy as np


@dataclass
class Frame:
    """A single decoded video frame.

    `image` is a numpy ndarray of shape (height, width, 3) in OpenCV's
    native BGR channel order, NOT RGB — a classic, easy source of bugs for
    anything downstream (detection, tracking, display, etc.) that assumes
    RGB. Convert explicitly (e.g. `cv2.cvtColor(image, cv2.COLOR_BGR2RGB)`)
    if a consumer needs RGB.
    """

    frame_number: int  # 0-indexed, original position in the source video
    timestamp_seconds: float
    image: np.ndarray
    width: int
    height: int


@dataclass
class VideoMetadata:
    fps: float
    frame_count: int
    duration_seconds: float
    width: int
    height: int
