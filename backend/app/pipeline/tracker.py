from abc import ABC, abstractmethod

from app.pipeline.detection import DetectionResult
from app.pipeline.track import TrackingResult


class Tracker(ABC):
    """Abstract contract for a multi-object tracker (master spec §28's
    module-contract pattern).

    ByteTrackAdapter (wrapping trackers.ByteTrackTracker) is the only
    concrete implementation in this codebase today. BoT-SORT+ReID is
    reserved for chokepoint zones (§5) — a future implementation, not built
    now; zones don't exist yet.

    HARD REQUIREMENT (§9/§31, constitutional): a Tracker instance is
    STATEFUL — it accumulates track identity/trajectory state across
    update() calls — and must be constructed fresh for each video/session.
    NEVER reuse one Tracker instance across two different videos: doing so
    would both break tracking correctness (unrelated videos' motion would
    be associated with each other) and violate the constitutional
    requirement that a track_id never persist beyond one video's
    processing. Each new video/session must get its own new Tracker
    instance.
    """

    @abstractmethod
    def update(self, detections: DetectionResult) -> TrackingResult: ...
