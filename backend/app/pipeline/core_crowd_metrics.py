"""Partial Crowd Metrics bundle — Phase 9, first half of roadmap Phase 11
(Crowd Intelligence Engine).

`CoreCrowdMetrics` is a DOCUMENTED SUBSET of the full future CrowdMetrics
contract from master spec §28
(`CrowdIntelligence.analyze(tracking, motion, temporal_state) -> CrowdMetrics`).
This phase covers Density (KDE + Voronoi), Flow (direction/magnitude/
divergence/curl), and Crowd Pressure only. Congestion, Bottleneck, Reverse
Flow, Risk Score, and the composite `confidence` field (distinct from this
phase's own per-field `DensityField.estimation_confidence`) are added by a
LATER phase that does not exist yet — this bundle must never be presented,
here or by any future caller, as the complete §28 contract.
"""

from dataclasses import dataclass

from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.crowd_pressure import CrowdPressureField, compute_crowd_pressure_field
from app.pipeline.density import DensityField, compute_density_field
from app.pipeline.flow_field import FlowGridField, compute_flow_grid_field
from app.pipeline.motion import MotionResult
from app.pipeline.track import TrackingResult


@dataclass
class CoreCrowdMetrics:
    frame_number: int
    timestamp_seconds: float
    density: DensityField
    flow: FlowGridField
    pressure: CrowdPressureField


def compute_core_crowd_metrics(
    tracking_result: TrackingResult,
    motion_result: MotionResult,
    frame_width: int,
    frame_height: int,
    elapsed_seconds: float,
) -> CoreCrowdMetrics:
    """`elapsed_seconds` (curr_frame.timestamp_seconds -
    prev_frame.timestamp_seconds, from the actual Frame objects the caller
    already has) is required in addition to the tracking/motion results
    themselves because MotionResult (Phase 8) does not store the previous
    frame's timestamp — only its frame_number — so the real elapsed time
    cannot be derived from MotionResult alone without assuming a uniform
    fps (see flow_field.py's docstring for the full explanation)."""
    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)

    density = compute_density_field(tracking_result, grid)
    flow = compute_flow_grid_field(motion_result, grid, elapsed_seconds)
    pressure = compute_crowd_pressure_field(density, flow)

    return CoreCrowdMetrics(
        frame_number=tracking_result.frame_number,
        timestamp_seconds=tracking_result.timestamp_seconds,
        density=density,
        flow=flow,
        pressure=pressure,
    )
