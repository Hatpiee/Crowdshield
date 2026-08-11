"""Bottleneck (master spec §6/§11, roadmap Phase 11 part 2): a Lagrangian
(tracer-advection-based) convergence signal, deliberately NOT the Eulerian
divergence already computed in Phase 9's flow_field.py. Divergence measures
the instantaneous, single-frame expansion/contraction rate of the velocity
field AT a point; it says nothing about whether people are actually
accumulating there OVER TIME. A cell can have near-zero instantaneous
divergence every single frame while still being a genuine bottleneck, if
motion is consistently funneling people toward it frame after frame — that
requires literally following (advecting) virtual tracers through the
velocity field across MULTIPLE frames, which is what this module does.

============================================================
SIMPLIFIED LAGRANGIAN METHOD — NOT A RIGOROUS FTLE IMPLEMENTATION
============================================================
This is an intentional MVP simplification, not an attempt at the academic
finite-time Lyapunov exponent (FTLE) method:
  - Forward-Euler integration only (position += velocity * dt) — no
    higher-order integrator (RK4, etc.). Forward-Euler accumulates more
    numerical error per step, especially with the coarse per-cell velocity
    field and the (often ~1/fps-sized) timesteps between frames, but is
    simple, cheap, and adequate for a directional convergence SIGNAL rather
    than a precise trajectory.
  - Tracers are re-seeded at cell centers at the start of every rolling
    window (decision #3), not carried forward indefinitely — this bounds
    memory/compute and avoids tracers drifting arbitrarily far from their
    origin cell over a long video.
  - "Spread" is a simple mean-distance-from-centroid statistic over a
    cell's own tracer plus its immediate (8-connected) neighbors' tracers —
    not a rigorously derived deformation-gradient eigenvalue as true FTLE
    would use.

DIMENSIONLESS RATIO — NO PIXEL CALIBRATION NEEDED (decision #3's own note):
`bottleneck_score_grid` is FINAL spread / INITIAL spread, both measured in
the same pixel units, so the ratio itself is unitless and self-normalizing
— unlike CROWD_GRID_CELL_SIZE_PX or the Congestion thresholds, this scoring
mechanism needs no separate pixel-space calibration constant. A ratio near
0 means strong convergence (tracers that started evenly spaced collapsed
toward each other); a ratio near or above 1.0 means no convergence
(uniform/parallel flow preserves relative spacing) or active dispersal.

STATEFUL (decision #1, same rule as Phase 7's Tracker): a BottleneckDetector
instance accumulates a rolling window of FlowGridField snapshots across
update() calls. Construct ONE fresh instance per video/session; NEVER reuse
across two different videos.
"""

from collections import deque
from dataclasses import dataclass

import numpy as np
from scipy.interpolate import RegularGridInterpolator

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.flow_field import FlowGridField


@dataclass
class BottleneckField:
    frame_number: int
    timestamp_seconds: float
    window_frames_used: int
    bottleneck_score_grid: np.ndarray  # shape (rows, cols); lower = stronger bottleneck
    strongest_bottleneck_cell: tuple[int, int] | None  # None only if every cell is isolated


def _spread_grid(positions: np.ndarray, rows: int, cols: int) -> np.ndarray:
    """Mean distance from centroid, per cell, over that cell's own tracer
    plus its immediate (8-connected) neighbors' tracers. NaN for a cell
    with no in-bounds neighbors at all (only possible on a degenerate
    1-row or 1-col grid) — never fabricated as 0 or 1."""
    spread = np.full((rows, cols), np.nan)
    for row in range(rows):
        for col in range(cols):
            group = [
                (r, c)
                for r in (row - 1, row, row + 1)
                for c in (col - 1, col, col + 1)
                if 0 <= r < rows and 0 <= c < cols
            ]
            if len(group) < 2:
                continue
            points = np.array([positions[r, c] for r, c in group])
            centroid = points.mean(axis=0)
            spread[row, col] = float(np.mean(np.linalg.norm(points - centroid, axis=1)))
    return spread


def _advect_tracers(
    velocity_snapshots: list[np.ndarray], timestamps: list[float], grid: CrowdGrid
) -> np.ndarray:
    """Seeds one tracer at every cell center, then forward-Euler-advects all
    of them together through each successive snapshot's velocity field
    (decision #3). Bilinear interpolation (scipy.interpolate) samples each
    snapshot's per-cell velocity at each tracer's current (possibly
    sub-cell) position; query positions are clamped into the grid's cell-
    center coordinate range so interpolation never has to extrapolate
    beyond the field it was built from."""
    row_centers = (np.arange(grid.rows) + 0.5) * grid.cell_height_px
    col_centers = (np.arange(grid.cols) + 0.5) * grid.cell_width_px
    xx, yy = grid.cell_center_grid()
    positions = np.stack([xx, yy], axis=-1).astype(float)  # (rows, cols, 2) = (x, y)

    x_min, x_max = col_centers[0], col_centers[-1]
    y_min, y_max = row_centers[0], row_centers[-1]

    for i in range(len(velocity_snapshots) - 1):
        dt = timestamps[i + 1] - timestamps[i]
        if dt <= 0:
            # Out-of-order/duplicate timestamps should never happen within
            # a single continuous video pass — defensively skip rather than
            # integrate backward or crash.
            continue

        velocity = velocity_snapshots[i]
        vx_interp = RegularGridInterpolator(
            (row_centers, col_centers), velocity[..., 0], bounds_error=False, fill_value=None
        )
        vy_interp = RegularGridInterpolator(
            (row_centers, col_centers), velocity[..., 1], bounds_error=False, fill_value=None
        )

        query_x = np.clip(positions[..., 0], x_min, x_max)
        query_y = np.clip(positions[..., 1], y_min, y_max)
        query_points = np.stack([query_y.ravel(), query_x.ravel()], axis=-1)

        vx = vx_interp(query_points).reshape(grid.rows, grid.cols)
        vy = vy_interp(query_points).reshape(grid.rows, grid.cols)

        positions[..., 0] += vx * dt
        positions[..., 1] += vy * dt

    return positions


class BottleneckDetector:
    """STATEFUL — construct one fresh instance per video/session, update()
    incrementally as each new frame's FlowGridField arrives, and NEVER
    reuse across two different videos (same hard requirement as Phase 7's
    Tracker — see tracker.py's docstring for why). Requires the video's
    CrowdGrid at construction (frame dimensions are fixed for the whole
    video/session, so this is knowable up front, same as ByteTrackAdapter
    needing the video's fps up front).
    """

    def __init__(self, grid: CrowdGrid):
        self._grid = grid
        self._velocity_window: deque[np.ndarray] = deque(
            maxlen=settings.BOTTLENECK_WINDOW_FRAMES
        )
        self._timestamp_window: deque[float] = deque(maxlen=settings.BOTTLENECK_WINDOW_FRAMES)

        xx, yy = grid.cell_center_grid()
        initial_positions = np.stack([xx, yy], axis=-1).astype(float)
        # Fixed by grid geometry alone (cell centers are evenly spaced by
        # construction) — computed once, never changes for this instance.
        self._initial_spread_grid = _spread_grid(initial_positions, grid.rows, grid.cols)

    def update(self, flow_grid_field: FlowGridField) -> BottleneckField | None:
        self._velocity_window.append(flow_grid_field.grid_mean_velocity)
        self._timestamp_window.append(flow_grid_field.timestamp_seconds)

        if len(self._velocity_window) < 2:
            # Fewer than 2 snapshots -> zero advection steps possible ->
            # nothing honest to report yet (never fabricate a result from
            # insufficient data).
            return None

        final_positions = _advect_tracers(
            list(self._velocity_window), list(self._timestamp_window), self._grid
        )
        final_spread_grid = _spread_grid(final_positions, self._grid.rows, self._grid.cols)

        with np.errstate(divide="ignore", invalid="ignore"):
            bottleneck_score_grid = final_spread_grid / self._initial_spread_grid

        if np.all(np.isnan(bottleneck_score_grid)):
            strongest_bottleneck_cell = None
        else:
            flat_index = np.nanargmin(bottleneck_score_grid)
            strongest_bottleneck_cell = tuple(
                int(i) for i in np.unravel_index(flat_index, bottleneck_score_grid.shape)
            )

        return BottleneckField(
            frame_number=flow_grid_field.frame_number,
            timestamp_seconds=flow_grid_field.timestamp_seconds,
            window_frames_used=len(self._velocity_window),
            bottleneck_score_grid=bottleneck_score_grid,
            strongest_bottleneck_cell=strongest_bottleneck_cell,
        )
