"""Shared test helper: builds a minimal but structurally complete
CrowdMetrics (Phase 9-11's full contract) for exercising the Phase 12
heatmap service/API layers without needing to run the real detection/
tracking/optical-flow pipeline. Shared by test_heatmap_service.py and
test_heatmaps_api.py so the construction logic isn't duplicated.
"""

import numpy as np

from app.core.config import settings
from app.pipeline.congestion import CongestionField
from app.pipeline.core_crowd_metrics import CoreCrowdMetrics
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import DensityField
from app.pipeline.flow_field import FlowGridField
from app.pipeline.predictive_projection import PredictiveProjection
from app.pipeline.reverse_flow import ReverseFlowField
from app.pipeline.risk_score import RiskScoreResult

# Must match what CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
# actually produces at the default CROWD_GRID_CELL_SIZE_PX=40 (240/40=6,
# 320/40=8) — render_flow_congestion_heatmap reconstructs this grid
# internally from frame dimensions alone, so hand-built fields must use the
# matching shape.
ROWS, COLS = 6, 8
FRAME_WIDTH, FRAME_HEIGHT = 320, 240


def make_crowd_metrics(
    frame_number: int, timestamp_seconds: float, with_projection: bool
) -> CrowdMetrics:
    grid = np.ones((ROWS, COLS))
    density = DensityField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        grid=grid,
        track_count=int(grid.sum()),
        estimation_confidence=1.0,
        degradation_reason=None,
        voronoi_disagreement_summary=None,
    )
    flow = FlowGridField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        grid_mean_velocity=np.zeros((ROWS, COLS, 2)),
        grid_velocity_variance=np.zeros((ROWS, COLS)),
        grid_divergence=np.zeros((ROWS, COLS)),
        grid_curl=np.zeros((ROWS, COLS)),
        source_motion_frame_number=frame_number,
    )
    pressure = CrowdPressureField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        grid=grid,
        max_pressure=1.0,
        mean_pressure=1.0,
    )
    core = CoreCrowdMetrics(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        density=density,
        flow=flow,
        pressure=pressure,
    )
    congestion = CongestionField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        is_congested_grid=np.zeros((ROWS, COLS), dtype=bool),
        congestion_score_grid=np.zeros((ROWS, COLS)),
        congested_cell_fraction=0.0,
    )
    reverse_flow = ReverseFlowField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        is_reverse_flow_grid=np.zeros((ROWS, COLS), dtype=bool),
        reverse_flow_cell_fraction=0.0,
        cells_with_established_baseline=0,
    )
    risk_score = RiskScoreResult(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        risk_score=0.0,
        confidence=1.0,
        contributing_signals=["pressure", "congestion", "reverse_flow"],
        sub_scores={"pressure": 0.0, "congestion": 0.0, "reverse_flow": 0.0},
    )
    projection = None
    if with_projection:
        projection = PredictiveProjection(
            frame_number=frame_number,
            timestamp_seconds=timestamp_seconds,
            horizon_seconds=settings.PREDICTION_HORIZON_SECONDS,
            projected_pressure=1.0,
            r_squared=0.9,
            window_seconds_used=5.0,
            data_points_used=10,
        )
    return CrowdMetrics(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        core=core,
        congestion=congestion,
        bottleneck=None,  # not exercised here — heatmap_rendering.py's own
        # tests already cover the with/without-bottleneck risk combination.
        reverse_flow=reverse_flow,
        risk_score=risk_score,
        predictive_projection=projection,
    )
