"""Flow vector-field analysis (master spec §6): direction, magnitude,
divergence, curl — aggregated onto the SAME coarse CrowdGrid used by
Density, so Crowd Pressure can combine them pointwise (decision #1).

Divergence and curl are computed on this coarse grid's mean velocity
field, not the dense per-pixel flow field directly (decision #7):
pixel-level spatial derivatives would be extremely noisy, while the coarse
grid is both necessary (local velocity variance needs cells) and
sufficient (smoother, more physically meaningful at crowd scale).
numpy.gradient is called with EXPLICIT pixel spacing (cell_height_px,
cell_width_px) — using its default unit spacing would make divergence/curl
silently dependent on CROWD_GRID_CELL_SIZE_PX's chosen value, a subtle
config-dependent correctness bug this decision exists specifically to
prevent (see test_flow_field.py's grid-size-independence test).

UNITS (decision #8, Critical Units Disclosure): grid_mean_velocity is in
TRUE pixels/second — computed by dividing MotionResult's raw flow_field
displacement by the REAL elapsed wall-clock time between the two source
frames. This is deliberately NOT reused from Phase 8's own
MotionResult.mean_velocity, which was specified in pixels/frame-interval
(an inconsistency logged in DECISIONS.md) — this module derives velocity
fresh, correctly, from the raw dense field and real timestamps.

MotionResult (Phase 8) does not itself store the previous frame's
timestamp (only the previous frame's frame_number, for traceability), so
the real elapsed time cannot be reconstructed from MotionResult alone
without assuming a uniform fps — exactly the "never assume uniform frame
timing" principle Phase 7 established. Instead, `elapsed_seconds` is an
explicit required parameter here, meant to be computed by the caller as
`curr_frame.timestamp_seconds - prev_frame.timestamp_seconds` from the
actual Frame objects it already has on hand at the point optical flow was
computed (see core_crowd_metrics.py).
"""

from dataclasses import dataclass

import numpy as np

from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.motion import MotionResult


@dataclass
class FlowGridField:
    frame_number: int
    timestamp_seconds: float
    grid_mean_velocity: np.ndarray  # shape (rows, cols, 2), pixels/second
    grid_velocity_variance: np.ndarray  # shape (rows, cols), pixels^2/second^2 (LOCAL)
    grid_divergence: np.ndarray  # shape (rows, cols)
    grid_curl: np.ndarray  # shape (rows, cols)
    source_motion_frame_number: int  # traceability: which MotionResult this came from


def compute_flow_grid_field(
    motion_result: MotionResult, grid: CrowdGrid, elapsed_seconds: float
) -> FlowGridField:
    if elapsed_seconds <= 0:
        raise ValueError(
            f"elapsed_seconds must be > 0 to compute true pixels/second, got "
            f"{elapsed_seconds}"
        )

    # Dense per-pixel velocity in TRUE pixels/second (decision #8) — raw
    # displacement divided by the real elapsed time, not Phase 8's
    # frame-interval convention.
    velocity_per_pixel = motion_result.flow_field / elapsed_seconds
    speed_per_pixel = np.linalg.norm(velocity_per_pixel, axis=-1)

    grid_mean_velocity = np.zeros((grid.rows, grid.cols, 2), dtype=float)
    grid_velocity_variance = np.zeros((grid.rows, grid.cols), dtype=float)

    frame_height, frame_width = speed_per_pixel.shape
    for row in range(grid.rows):
        for col in range(grid.cols):
            x_min, y_min, x_max, y_max = grid.cell_bounds(row, col)
            x0, x1 = int(round(x_min)), min(int(round(x_max)), frame_width)
            y0, y1 = int(round(y_min)), min(int(round(y_max)), frame_height)
            if x1 <= x0 or y1 <= y0:
                continue  # degenerate cell (shouldn't happen with a sane grid)

            cell_velocity = velocity_per_pixel[y0:y1, x0:x1, :]
            cell_speed = speed_per_pixel[y0:y1, x0:x1]

            grid_mean_velocity[row, col, 0] = cell_velocity[..., 0].mean()
            grid_mean_velocity[row, col, 1] = cell_velocity[..., 1].mean()
            grid_velocity_variance[row, col] = cell_speed.var()

    # Divergence/curl on the coarse grid, explicit real pixel spacing
    # (decision #7) — axis 0 is rows (y-direction, spacing=cell_height_px),
    # axis 1 is cols (x-direction, spacing=cell_width_px).
    vx = grid_mean_velocity[..., 0]
    vy = grid_mean_velocity[..., 1]
    dvx_dy, dvx_dx = np.gradient(vx, grid.cell_height_px, grid.cell_width_px)
    dvy_dy, dvy_dx = np.gradient(vy, grid.cell_height_px, grid.cell_width_px)

    grid_divergence = dvx_dx + dvy_dy
    grid_curl = dvy_dx - dvx_dy

    return FlowGridField(
        frame_number=motion_result.frame_number,
        timestamp_seconds=motion_result.timestamp_seconds,
        grid_mean_velocity=grid_mean_velocity,
        grid_velocity_variance=grid_velocity_variance,
        grid_divergence=grid_divergence,
        grid_curl=grid_curl,
        source_motion_frame_number=motion_result.frame_number,
    )
