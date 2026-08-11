"""Track output data classes for the tracker pipeline stage.

`Track.point`/`local_scale`/`confidence` mirror Phase 6's `Detection` shape
— same head-anchored-point representation, now carried across frames under
a stable `track_id`. Per master spec §9/§31 (constitutional): `track_id` is
a temporary, internal, session-scoped identifier only — never real-world
identity, never persisted beyond one video's processing, never linked to
any person record. See `Tracker`'s docstring for the hard per-video
statefulness requirement this implies.
"""

from dataclasses import dataclass, field

from app.pipeline.detection import Point

TrajectoryPoint = tuple[int, float, Point]  # (frame_number, timestamp_seconds, point)


@dataclass
class Track:
    track_id: int
    point: Point
    local_scale: float
    confidence: float
    # Most recent TRACK_HISTORY_LENGTH (frame_number, timestamp_seconds,
    # point) entries only — bounded to avoid unbounded memory growth over a
    # long video.
    trajectory: list[TrajectoryPoint] = field(default_factory=list)
    # Pixels/second. None if fewer than two trajectory points exist yet —
    # never fabricated as zero (zero would falsely claim "not moving").
    velocity: tuple[float, float] | None = None
    # Pixels/second, scalar magnitude of velocity. Same None rule.
    speed: float | None = None
    # True if this track had no matching detection in the current frame but
    # is still retained within the tracker's lost-track buffer. When True,
    # point/local_scale/confidence are carried forward from the last real
    # match — never extrapolated to a fabricated new position.
    is_lost: bool = False
    # 0 if matched this frame; otherwise the number of consecutive frames
    # since this track was last matched to a real detection.
    frames_since_last_matched: int = 0


@dataclass
class TrackingResult:
    # Passed through from the input DetectionResult for traceability.
    frame_number: int
    timestamp_seconds: float
    tracks: list[Track] = field(default_factory=list)
    tracker_name: str = ""
    # How many detections were fed into this update() call — for
    # traceability/debugging (e.g. distinguishing "0 tracks because 0
    # detections came in" from "0 tracks because none were confirmed yet").
    source_detection_count: int = 0
