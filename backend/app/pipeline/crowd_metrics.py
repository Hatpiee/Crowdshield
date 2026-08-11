"""Complete Crowd Metrics bundle — Phase 11, the THIRD and FINAL part of
roadmap Phase 11 (Crowd Intelligence Engine). Phase 9 covered Density,
Flow, and Crowd Pressure; Phase 10 added Congestion, Bottleneck, and
Reverse Flow (both untouched by this file — extended here via
composition, never modified). This phase adds the composite Risk Score
and the Predictive Projection.

MILESTONE: `CrowdMetrics` NOW FULFILLS THE FULL §28 CONTRACT
(`CrowdIntelligence.analyze(tracking, motion, temporal_state) ->
CrowdMetrics`). Phases 9 and 10 both explicitly flagged this dataclass as
a documented SUBSET, deferring `risk_score` and a propagated `confidence`
— both now exist below. Nothing about the CrowdMetrics data contract
itself is deliberately deferred beyond this point. (Two things this
project has NOT built, and which are NOT part of the §28 CrowdMetrics data
contract, remain explicitly out of scope: the Risk State + Trigger Engine
that CONSUMES this contract to classify NORMAL/ELEVATED/CRITICAL/INCIDENT
with hysteresis, and the mandatory 5-heatmap-type VISUALIZATION system —
both later, separate phases that read this data, not additions to it.)

`CrowdMetricsEngine` is the concrete realization of §28's `temporal_state`
concept: BottleneckDetector, ReverseFlowDetector, and (as of this phase)
PressureProjector are all stateful, accumulating history across frames,
and this engine threads ONE of each through an entire video/session
(decision #1/#10) — never reused across two different videos, same hard
rule as every other stateful pipeline component in this project (Tracker,
BottleneckDetector, ReverseFlowDetector, PressureProjector individually).

Risk score computation ITSELF is STATELESS (decision #10) — a pure
function of a single frame's already-computed density/pressure/congestion/
bottleneck/reverse_flow (see risk_score.py's `compute_risk_score`). It
needs no state of its own beyond what BottleneckDetector/ReverseFlowDetector
already independently maintain — this engine calls it fresh every frame,
it does not accumulate anything across calls.
"""

from dataclasses import dataclass

from app.pipeline.bottleneck import BottleneckDetector, BottleneckField
from app.pipeline.congestion import CongestionField, compute_congestion_field
from app.pipeline.core_crowd_metrics import CoreCrowdMetrics, compute_core_crowd_metrics
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.motion import MotionResult
from app.pipeline.predictive_projection import PredictiveProjection, PressureProjector
from app.pipeline.reverse_flow import ReverseFlowDetector, ReverseFlowField
from app.pipeline.risk_score import RiskScoreResult, compute_risk_score
from app.pipeline.track import TrackingResult


@dataclass
class CrowdMetrics:
    frame_number: int
    timestamp_seconds: float
    core: CoreCrowdMetrics  # Phase 9: density, flow, pressure
    congestion: CongestionField  # Phase 10
    bottleneck: BottleneckField | None  # Phase 10; None until the window has enough history
    reverse_flow: ReverseFlowField  # Phase 10
    risk_score: RiskScoreResult  # Phase 11
    predictive_projection: PredictiveProjection | None  # Phase 11; None until enough history


class CrowdMetricsEngine:
    """Orchestrates Phase 9's compute_core_crowd_metrics, Phase 10's
    Congestion/Bottleneck/Reverse Flow, and this phase's Risk Score /
    Predictive Projection, threading ONE BottleneckDetector, ONE
    ReverseFlowDetector, and ONE PressureProjector instance across a whole
    video/session's worth of frames (decision #1/#10).

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
        self._pressure_projector = PressureProjector()

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

        risk_score = compute_risk_score(
            core.density, core.pressure, congestion, bottleneck, reverse_flow
        )
        predictive_projection = self._pressure_projector.update(core.pressure)

        return CrowdMetrics(
            frame_number=core.frame_number,
            timestamp_seconds=core.timestamp_seconds,
            core=core,
            congestion=congestion,
            bottleneck=bottleneck,
            reverse_flow=reverse_flow,
            risk_score=risk_score,
            predictive_projection=predictive_projection,
        )
