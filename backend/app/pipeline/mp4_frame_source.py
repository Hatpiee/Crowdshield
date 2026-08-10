from pathlib import Path
from typing import Iterator

import cv2

from app.pipeline.frame import Frame, VideoMetadata
from app.pipeline.frame_source import FrameSource


class VideoOpenError(Exception):
    pass


class InvalidFrameRateError(Exception):
    pass


class MP4FrameSource(FrameSource):
    """FrameSource for local MP4 files, backed by OpenCV's VideoCapture.

    `frame_step` defaults to 1 (yield every frame) — §4 requires
    detection/tracking/flow to run on every frame in the real pipeline, so
    this default must never be silently changed. frame_step > 1 exists as a
    general-purpose capability for potential future use (e.g. quick
    previews) but nothing in this codebase uses any value other than the
    default today.
    """

    def __init__(self, file_path: Path | str, frame_step: int = 1):
        if frame_step < 1:
            raise ValueError("frame_step must be >= 1")
        self._file_path = str(file_path)
        self._frame_step = frame_step
        self._capture: cv2.VideoCapture | None = None

    def __enter__(self) -> "MP4FrameSource":
        capture = cv2.VideoCapture(self._file_path)
        if not capture.isOpened():
            capture.release()
            raise VideoOpenError(f"Could not open video file: {self._file_path}")
        self._capture = capture
        return self

    def __exit__(self, exc_type, exc_val, exc_tb) -> None:
        # VideoCapture.release() must always run, exception or not — an
        # unreleased capture can hold a Windows file lock (this project has
        # already hit real, costly file-locking issues elsewhere).
        try:
            if self._capture is not None:
                self._capture.release()
        finally:
            self._capture = None

    def get_metadata(self) -> VideoMetadata:
        capture = self._require_capture()
        fps = capture.get(cv2.CAP_PROP_FPS)
        frame_count = int(capture.get(cv2.CAP_PROP_FRAME_COUNT))
        width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))

        if fps <= 0:
            raise InvalidFrameRateError(f"Video reports an invalid frame rate: {fps}")

        return VideoMetadata(
            fps=fps,
            frame_count=frame_count,
            duration_seconds=frame_count / fps,
            width=width,
            height=height,
        )

    def frames(self) -> Iterator[Frame]:
        capture = self._require_capture()
        fps = capture.get(cv2.CAP_PROP_FPS)

        frame_number = 0
        while True:
            ok, image = capture.read()
            if not ok:
                break
            if frame_number % self._frame_step == 0:
                height, width = image.shape[:2]
                yield Frame(
                    frame_number=frame_number,
                    timestamp_seconds=frame_number / fps if fps > 0 else 0.0,
                    image=image,
                    width=width,
                    height=height,
                )
            frame_number += 1

    def _require_capture(self) -> cv2.VideoCapture:
        if self._capture is None:
            raise RuntimeError(
                "MP4FrameSource must be used as a context manager: "
                "'with MP4FrameSource(path) as source: ...'"
            )
        return self._capture
