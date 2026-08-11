"""Extended Crowd Metrics bundle — Phase 10, second of a three-part split
of roadmap Phase 11 (Crowd Intelligence Engine). Phase 9 covered Density,
Flow, and Crowd Pressure (`core_crowd_metrics.py`, untouched by this file —
extended here via composition, never modified). This phase adds Congestion,
Bottleneck, and Reverse Flow.

`CrowdMetrics` is STILL a documented SUBSET of the full future §28
`CrowdMetrics` contract (`CrowdIntelligence.analyze(tracking, motion,
temporal_state) -> CrowdMetrics`). The composite `risk_score` and the
composite `confidence` field are added by a LATER phase (our Phase 11,
covering the Risk State + Trigger Engine's inputs) that does not exist yet
— this bundle must never be presented, here or by any future caller, as
the complete §28 contract.

`CrowdMetricsEngine` is the concrete realization of §28's `temporal_state`
concept for this phase: BottleneckDetector and ReverseFlowDetector are both
stateful, accumulating history across frames, and this engine threads ONE
of each through an entire video/session (decision #1) — never reused
across two different videos, same hard rule as every other stateful
pipeline component in this project (Tracker, BottleneckDetector,
ReverseFlowDetector individually).
"""

from dataclasses import dataclass

from app.pipeline.bottleneck import BottleneckDetector, BottleneckField
from app.pipeline.congestion import CongestionField, compute_congestion_field
from app.pipeline.core_crowd_metrics import CoreCrowdMetrics, compute_core_crowd_metrics
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.motion import MotionResult
from app.pipeline.reverse_flow import ReverseFlowDetector, ReverseFlowField
from app.pipeline.track import TrackingResult


@dataclass
class CrowdMetrics:
    frame_number: int
    timestamp_seconds: float
    core: CoreCrowdMetrics  # Phase 9: density, flow, pressure
    congestion: CongestionField
    bottleneck: BottleneckField | None  # None until BottleneckDetector's window has enough history
    reverse_flow: ReverseFlowField


class CrowdMetricsEngine:
    """Orchestrates Phase 9's compute_core_crowd_metrics together with this
    phase's Congestion/Bottleneck/Reverse Flow, threading ONE
    BottleneckDetector and ONE ReverseFlowDetector instance across a whole
    video/session's worth of frames (decision #1).

    STATEFUL — construct one fresh instance per video/session (frame
    dimensions are fixed for the whole video, so they're required up
    front, same as ByteTrackAdapter needing fps up front). NEVER reuse
    across two different videos.
    """

    def __init__(self, frame_width: int, frame_height: int):
        self._frame_width = frame_width
        self._frame_height = frame_height
        grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
        self._bottleneck_detector = BottleneckDetector(grid)
        self._reverse_flow_detector = ReverseFlowDetector(grid)

    def update(
        self,
        tracking_result: TrackingResult,
        motion_result: MotionResult,
        elapsed_seconds: float,
    ) -> CrowdMetrics:
        core = compute_core_crowd_metrics(
            tracking_result,
            motion_result,
            self._frame_width,
            self._frame_height,
            elapsed_seconds,
        )
        congestion = compute_congestion_field(core.density, core.flow)
        bottleneck = self._bottleneck_detector.update(core.flow)
        reverse_flow = self._reverse_flow_detector.update(core.flow)

        return CrowdMetrics(
            frame_number=core.frame_number,
            timestamp_seconds=core.timestamp_seconds,
            core=core,
            congestion=congestion,
            bottleneck=bottleneck,
            reverse_flow=reverse_flow,
        )
