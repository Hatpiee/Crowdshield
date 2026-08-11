"""Motion output data class for the optical flow pipeline stage.

See `MotionResult`'s own docstring for the critical distinction between its
primary payload (the raw per-pixel flow field) and its summary statistics
(whole-frame scalars) — they serve very different downstream consumers and
must not be confused.
"""

from dataclasses import dataclass

import numpy as np


@dataclass
class MotionResult:
    """Output of one OpticalFlow.compute(prev_frame, curr_frame) call.

    PRIMARY PAYLOAD: `flow_field`, a dense per-pixel (H, W, 2) float32 array
    of (dx, dy) displacement. Master spec §12's Crowd Pressure formula
    (Pressure(r,t) = density(r,t) x velocity_variance(r,t)) is explicitly
    LOCAL/spatially-indexed — a later phase (Crowd Intelligence Engine,
    roadmap Phase 11) computes genuinely local statistics (including
    divergence and curl, explicitly NOT this phase's job) FROM this raw
    field. This phase does not know about regions/zones at all.

    SUMMARY STATISTICS: `mean_velocity`, `velocity_variance`,
    `dominant_direction_degrees`, `directional_entropy` are ADDITIONAL
    whole-frame convenience/traceability scalars computed FROM flow_field
    by this phase, for observability/debugging — NOT a substitute for the
    local, spatially-indexed velocity_variance(r,t) that Crowd Pressure
    actually requires. In particular, `velocity_variance` here is a single
    GLOBAL number describing the whole frame's motion spread, not a
    per-region value. Do not wire this field into any future Crowd Pressure
    computation under the assumption it's already "the" variance needed —
    it isn't.

    All four summary statistics are computed only from pixels whose flow
    magnitude clears MOTION_MAGNITUDE_NOISE_FLOOR (see
    dis_optical_flow.py) — the raw flow_field itself is never filtered.
    dominant_direction_degrees/directional_entropy are None (never
    fabricated as 0) when zero pixels clear the floor.
    """

    frame_number: int  # curr_frame's frame_number
    prev_frame_number: int
    timestamp_seconds: float  # curr_frame's timestamp_seconds
    flow_field: np.ndarray  # shape (H, W, 2), dtype float32, per-pixel (dx, dy)
    mean_velocity: float  # pixels/frame-interval, noise-floor-filtered
    velocity_variance: float  # GLOBAL whole-frame statistic — see docstring above
    dominant_direction_degrees: float | None  # 0-360, magnitude-weighted circular mean
    directional_entropy: float | None  # Shannon entropy of weighted direction histogram
    preset_used: str
    noise_floor_used: float
