"""Congestion (master spec §6/§11, roadmap Phase 11 part 2): a per-cell
signal combining HIGH density with LOW flow magnitude — deliberately NOT
density alone (frozen decision). A packed cell where people are still
moving through at a normal pace is a managed, flowing queue; a packed cell
where motion has stalled is the more dangerous pattern (people jammed in
place, unable to move) — Congestion is meant to distinguish the two.

============================================================
PIXEL-SPACE UNITS DISCLOSURE (carried forward from Phase 9)
============================================================
DENSITY_CONGESTION_THRESHOLD (people/cell) and
FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC (pixels/second) are BOTH
pixel-space-native and uncalibrated against any real venue, for the exact
same reason as Phase 9's CROWD_GRID_CELL_SIZE_PX and Crowd Pressure: this
project has no camera calibration/homography step. See DECISIONS.md's
"Known Structural Limitation: Pixel-Space vs. Real-World Units" section —
that limitation applies to both new thresholds introduced here.

FORMULA (decision #2): for each grid cell,
    normalized_density = clip(density / DENSITY_CONGESTION_THRESHOLD, 0, 1)
    normalized_flow    = clip(speed / FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC, 0, 1)
    congestion_score   = normalized_density * (1 - normalized_flow)
    is_congested       = (density >= DENSITY_CONGESTION_THRESHOLD)
                          AND (speed <= FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC)

`congestion_score` is a smooth 0-1 signal (drops to 0 the moment flow speed
reaches the threshold, regardless of how dense the cell is — this is what
makes it density-AND-flow rather than density-alone). `is_congested` is the
hard boolean version of the same two-condition AND, used for the
self-normalizing `congested_cell_fraction` summary.
"""

from dataclasses import dataclass

import numpy as np

from app.core.config import settings
from app.pipeline.density import DensityField
from app.pipeline.flow_field import FlowGridField


@dataclass
class CongestionField:
    frame_number: int
    timestamp_seconds: float
    is_congested_grid: np.ndarray  # shape (rows, cols), bool
    congestion_score_grid: np.ndarray  # shape (rows, cols), float, 0-1
    congested_cell_fraction: float  # 0-1, fraction of cells flagged congested


def compute_congestion_field(
    density: DensityField, flow: FlowGridField
) -> CongestionField:
    speed_grid = np.linalg.norm(flow.grid_mean_velocity, axis=-1)

    if density.grid.shape != speed_grid.shape:
        raise ValueError(
            "DensityField.grid and FlowGridField.grid_mean_velocity's spatial "
            f"shape must match to combine pointwise; got {density.grid.shape} "
            f"vs {speed_grid.shape}"
        )

    density_threshold = settings.DENSITY_CONGESTION_THRESHOLD
    flow_threshold = settings.FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC

    normalized_density = np.clip(density.grid / density_threshold, 0.0, 1.0)
    normalized_flow = np.clip(speed_grid / flow_threshold, 0.0, 1.0)
    congestion_score_grid = normalized_density * (1.0 - normalized_flow)

    is_congested_grid = (density.grid >= density_threshold) & (
        speed_grid <= flow_threshold
    )

    return CongestionField(
        frame_number=density.frame_number,
        timestamp_seconds=density.timestamp_seconds,
        is_congested_grid=is_congested_grid,
        congestion_score_grid=congestion_score_grid,
        congested_cell_fraction=float(is_congested_grid.mean()),
    )
