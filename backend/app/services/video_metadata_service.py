from pathlib import Path

from app.pipeline.frame import VideoMetadata
from app.pipeline.mp4_frame_source import (
    InvalidFrameRateError,
    MP4FrameSource,
    VideoOpenError,
)


class UnreadableVideoError(Exception):
    pass


def extract_metadata(file_path: Path) -> VideoMetadata:
    """Opens `file_path` and returns its VideoMetadata, or raises
    UnreadableVideoError if the file cannot be opened at all, reports an
    invalid frame rate, or reports zero frames — meaning it passed the
    cheap magic-byte check but is not actually a genuinely decodable video.

    Does not catch UnreadableVideoError itself; the caller (the API layer)
    decides how to respond, per the layered architecture requirement (§23).
    """
    try:
        with MP4FrameSource(file_path) as source:
            metadata = source.get_metadata()
    except (VideoOpenError, InvalidFrameRateError) as exc:
        raise UnreadableVideoError(str(exc)) from exc

    if metadata.frame_count <= 0:
        raise UnreadableVideoError(
            f"Video reports an invalid frame count: {metadata.frame_count}"
        )

    return metadata
