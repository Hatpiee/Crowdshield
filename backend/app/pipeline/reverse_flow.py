"""Reverse Flow (master spec §6/§11, roadmap Phase 11 part 2): per-zone
learned direction baseline + statistical deviation + temporal persistence,
explicitly adapted from vehicle wrong-way-detection literature. The master
spec itself flags this mechanism as needing pedestrian-domain validation —
this module implements the described MECHANISM honestly; it does not claim
validated correctness for crowds anywhere in this code or its docs.

UNITS NOTE: unlike Congestion's two thresholds, none of this module's four
new configurable parameters (EMA alpha, minimum baseline observations,
deviation threshold in DEGREES, persistence window/count) are pixel-space
quantities — they govern a learning rate, an observation count, and an
ANGLE, all of which are unit-agnostic. The Phase 9/Congestion pixel-space
units disclosure does NOT apply here; flagging that explicitly so it isn't
over-applied where it doesn't belong.

MECHANISM (decision #4):
  1. Per grid cell, maintain an exponential-moving-average "baseline
     direction" as a 2D unit-vector EMA (avoids angle-wraparound issues an
     EMA over raw angles would have). Updated only for cells with a
     defined direction THIS frame (speed > 0) — a cell with no motion this
     frame contributes nothing and does not corrupt the baseline.
  2. A cell's baseline is "established" only once REVERSE_FLOW_MIN_BASELINE_
     OBSERVATIONS updates have actually happened for it. Before that,
     reverse flow is never flagged for that cell (avoids false positives
     from an unformed/noisy baseline).
  3. Each frame, for an established cell with motion, the angular
     deviation between THIS frame's direction and the EXISTING baseline
     (measured before this frame's own contribution is folded in — a cell
     is never checked against a baseline that already includes itself)
     is computed via the cosine of the angle between the two unit vectors.
     If that deviation exceeds REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES,
     the frame is "locally reversed" for that cell.
  4. A small rolling boolean window (REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES)
     of "locally reversed" flags is kept per cell; `is_reverse_flow` is
     only set True once at least REVERSE_FLOW_PERSISTENCE_MIN_COUNT of the
     last window's frames were locally reversed. This temporal persistence
     requirement is distinct from and unrelated to the later Trigger
     Engine's own separate hysteresis on the composite risk score — it
     exists here specifically to prevent single-frame optical-flow noise
     from reading as a sustained wrong-way flow event.
"""

from collections import deque
from dataclasses import dataclass

import numpy as np

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.flow_field import FlowGridField

# Numerical safety floor only (NOT an engineering-judgment tunable, not
# logged in DECISIONS.md) — distinguishes genuinely-zero velocity (no
# defined direction to normalize) from floating-point noise.
_ZERO_SPEED_EPSILON = 1e-9


@dataclass
class ReverseFlowField:
    frame_number: int
    timestamp_seconds: float
    is_reverse_flow_grid: np.ndarray  # shape (rows, cols), bool
    reverse_flow_cell_fraction: float  # 0-1, fraction of cells flagged reverse-flow
    cells_with_established_baseline: int  # diagnostic: how many cells can be checked at all


class ReverseFlowDetector:
    """STATEFUL — construct one fresh instance per video/session, update()
    incrementally as each new frame's FlowGridField arrives, and NEVER
    reuse across two different videos (same hard requirement as Phase 7's
    Tracker and this phase's BottleneckDetector).
    """

    def __init__(self, grid: CrowdGrid):
        self._grid = grid
        shape = (grid.rows, grid.cols)
        self._baseline_vector = np.zeros((*shape, 2), dtype=float)
        self._observation_count = np.zeros(shape, dtype=int)
        self._persistence_history: deque[np.ndarray] = deque(
            maxlen=settings.REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES
        )

    def update(self, flow_grid_field: FlowGridField) -> ReverseFlowField:
        velocity = flow_grid_field.grid_mean_velocity  # (rows, cols, 2)
        speed = np.linalg.norm(velocity, axis=-1)
        has_motion = speed > _ZERO_SPEED_EPSILON

        unit_direction = np.zeros_like(velocity)
        unit_direction[has_motion] = velocity[has_motion] / speed[has_motion, None]

        # Established/deviation check uses the baseline as it stood BEFORE
        # this frame's own contribution is folded in (below) — a cell must
        # never be checked against a baseline that already includes itself.
        established_before_update = self._observation_count >= settings.REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS
        baseline_norm = np.linalg.norm(self._baseline_vector, axis=-1)
        has_baseline_direction = baseline_norm > _ZERO_SPEED_EPSILON

        checkable = has_motion & established_before_update & has_baseline_direction

        cos_deviation = np.zeros((self._grid.rows, self._grid.cols), dtype=float)
        baseline_unit = np.zeros_like(self._baseline_vector)
        baseline_unit[has_baseline_direction] = (
            self._baseline_vector[has_baseline_direction]
            / baseline_norm[has_baseline_direction, None]
        )
        cos_deviation[checkable] = np.sum(
            unit_direction[checkable] * baseline_unit[checkable], axis=-1
        )
        deviation_degrees = np.degrees(np.arccos(np.clip(cos_deviation, -1.0, 1.0)))

        locally_reversed = checkable & (
            deviation_degrees > settings.REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES
        )
        self._persistence_history.append(locally_reversed)

        persistence_count = np.sum(np.stack(list(self._persistence_history), axis=0), axis=0)
        is_reverse_flow_grid = established_before_update & (
            persistence_count >= settings.REVERSE_FLOW_PERSISTENCE_MIN_COUNT
        )

        # EMA update happens LAST, so it reflects this frame's contribution
        # for the NEXT call — cells with no motion this frame are skipped
        # entirely (decision #4), leaving their baseline/observation count
        # untouched.
        alpha = settings.REVERSE_FLOW_BASELINE_EMA_ALPHA
        self._baseline_vector[has_motion] = (
            alpha * unit_direction[has_motion] + (1 - alpha) * self._baseline_vector[has_motion]
        )
        self._observation_count[has_motion] += 1

        established_after_update = (
            self._observation_count >= settings.REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS
        )

        return ReverseFlowField(
            frame_number=flow_grid_field.frame_number,
            timestamp_seconds=flow_grid_field.timestamp_seconds,
            is_reverse_flow_grid=is_reverse_flow_grid,
            reverse_flow_cell_fraction=float(is_reverse_flow_grid.mean()),
            cells_with_established_baseline=int(established_after_update.sum()),
        )
