"""ROI selection — roadmap Phase 14, decision #1. Finds the pixel region a
triggered VLM call should actually look closely at: the ARGMAX cell of
Phase 14's shared `compute_risk_grid` (risk_score.py, the same per-cell
formula Phase 12's Risk heatmap renders), expanded by a CONFIGURABLE
margin to give the VLM enough visual context beyond one small grid cell,
clamped to stay within frame bounds.

Rationale for reusing compute_risk_grid rather than a simpler alternative
(e.g. raw Crowd Pressure argmax): keeps "why did we look here" coherent
with "why did the trigger fire" — both trace back to the exact same
composite risk signal (Phase 11/13's risk_score, spatialized).
"""

import numpy as np

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid


def select_roi(
    risk_grid: np.ndarray, crowd_grid: CrowdGrid, frame_width: int, frame_height: int
) -> tuple[float, float, float, float]:
    """Returns (x_min, y_min, x_max, y_max) — a PIXEL-space bounding box,
    clamped to [0, frame_width] x [0, frame_height].

    UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md):
    ROI_EXPANSION_FACTOR (default 3.0) scales the argmax cell's own pixel
    footprint uniformly around its center — e.g. at the default, a cell is
    expanded to 3x its own width and height, still centered on the same
    point. Not tuned against any real "was this crop actually useful to
    the VLM" evaluation.
    """
    if risk_grid.shape != (crowd_grid.rows, crowd_grid.cols):
        raise ValueError(
            f"risk_grid shape {risk_grid.shape} does not match crowd_grid "
            f"({crowd_grid.rows}, {crowd_grid.cols})"
        )

    flat_argmax = int(np.argmax(risk_grid))
    row, col = divmod(flat_argmax, crowd_grid.cols)

    x_min, y_min, x_max, y_max = crowd_grid.cell_bounds(row, col)
    cell_width = x_max - x_min
    cell_height = y_max - y_min
    center_x, center_y = crowd_grid.cell_center(row, col)

    half_width = (cell_width * settings.ROI_EXPANSION_FACTOR) / 2.0
    half_height = (cell_height * settings.ROI_EXPANSION_FACTOR) / 2.0

    roi_x_min = max(0.0, center_x - half_width)
    roi_y_min = max(0.0, center_y - half_height)
    roi_x_max = min(float(frame_width), center_x + half_width)
    roi_y_max = min(float(frame_height), center_y + half_height)

    return (roi_x_min, roi_y_min, roi_x_max, roi_y_max)
