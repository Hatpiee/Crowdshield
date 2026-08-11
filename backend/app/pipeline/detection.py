"""Detection output data classes for the detector pipeline stage.

`Detection.point` is the system's official, exposed representation per
master spec §9 ("Identity: No face recognition, no naming, no
cross-camera Re-ID, no biometric ID" — a head-anchored point, never a
full-body bounding box, is what this system reports downstream).

`Detection.raw_box` is a secondary/internal field. Rationale: Tracking
(ByteTrack, roadmap Phase 9) is not yet designed in this project, and
standard ByteTrack implementations are box-based; discarding box data
now could force redoing detection output later if Phase 9 needs it.
This does NOT violate the head-anchored-point representation principle
— the point remains the system's official representation, and raw_box
must never be surfaced to any consumer as "the" detection output. This
tradeoff will be revisited once Tracking is actually designed.
"""

from dataclasses import dataclass, field


@dataclass
class Point:
    x: float
    y: float


@dataclass
class Detection:
    point: Point
    local_scale: float
    confidence: float
    # Internal/secondary, tracking-reserved field (see module docstring)
    # — NOT part of the official head-anchored-point representation.
    raw_box: tuple[float, float, float, float] | None = None


@dataclass
class DetectionResult:
    # Passed through from the input Frame for traceability.
    frame_number: int
    timestamp_seconds: float
    detections: list[Detection] = field(default_factory=list)
    # Confidence propagation, never invented: metadata about what
    # produced this result travels with it.
    model_name: str = ""
    confidence_threshold_used: float = 0.0
