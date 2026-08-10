from abc import ABC, abstractmethod
from typing import Iterator

from app.pipeline.frame import Frame, VideoMetadata


class FrameSource(ABC):
    """Abstract contract for a video frame source (master spec §28's
    module-contract pattern; §8's ingestion abstraction requirement).

    This interface is written generally enough that a future
    RTSPFrameSource (V2, not built in this phase) could implement it
    without requiring any change to downstream consumers — MP4FrameSource
    is the only concrete implementation in this codebase today (MP4-only,
    V1 scope; no RTSP code exists anywhere here).

    A FrameSource is a context manager: __enter__ opens the underlying
    resource, __exit__ must guarantee it is released even on exception.
    """

    @abstractmethod
    def __enter__(self) -> "FrameSource": ...

    @abstractmethod
    def __exit__(self, exc_type, exc_val, exc_tb) -> None: ...

    @abstractmethod
    def get_metadata(self) -> VideoMetadata: ...

    @abstractmethod
    def frames(self) -> Iterator[Frame]:
        """Yields frames one at a time — a generator, not a list, so large
        videos are never fully loaded into memory at once."""
        ...
