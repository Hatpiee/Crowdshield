"""Phase 14, Step 6: roi_selection.py — argmax-cell-plus-expansion logic
(decision #1), clamped to frame bounds.
"""

import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.roi_selection import select_roi


def test_select_roi_centers_on_argmax_cell_and_expands_by_factor():
    frame_width, frame_height = 400, 200
    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
    risk_grid = np.zeros((grid.rows, grid.cols))
    target_row, target_col = grid.rows // 2, grid.cols // 2  # safely interior cell
    risk_grid[target_row, target_col] = 100.0

    x_min, y_min, x_max, y_max = select_roi(risk_grid, grid, frame_width, frame_height)

    cell_x_min, cell_y_min, cell_x_max, cell_y_max = grid.cell_bounds(target_row, target_col)
    center_x, center_y = grid.cell_center(target_row, target_col)
    cell_width = cell_x_max - cell_x_min
    cell_height = cell_y_max - cell_y_min

    expected_half_width = (cell_width * settings.ROI_EXPANSION_FACTOR) / 2.0
    expected_half_height = (cell_height * settings.ROI_EXPANSION_FACTOR) / 2.0

    assert x_min == pytest.approx(center_x - expected_half_width)
    assert x_max == pytest.approx(center_x + expected_half_width)
    assert y_min == pytest.approx(center_y - expected_half_height)
    assert y_max == pytest.approx(center_y + expected_half_height)

    # Sanity: selected box is centered on the argmax cell's own center.
    assert (x_min + x_max) / 2.0 == pytest.approx(center_x)
    assert (y_min + y_max) / 2.0 == pytest.approx(center_y)


def test_select_roi_clamps_to_frame_bounds_near_edge():
    frame_width, frame_height = 400, 200
    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
    risk_grid = np.zeros((grid.rows, grid.cols))
    risk_grid[0, 0] = 100.0  # top-left corner cell — expansion would overshoot both edges

    x_min, y_min, x_max, y_max = select_roi(risk_grid, grid, frame_width, frame_height)

    assert x_min == 0.0
    assert y_min == 0.0
    assert 0.0 <= x_max <= frame_width
    assert 0.0 <= y_max <= frame_height


def test_select_roi_clamps_at_bottom_right_edge():
    frame_width, frame_height = 400, 200
    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
    risk_grid = np.zeros((grid.rows, grid.cols))
    risk_grid[grid.rows - 1, grid.cols - 1] = 100.0  # bottom-right corner cell

    x_min, y_min, x_max, y_max = select_roi(risk_grid, grid, frame_width, frame_height)

    assert x_max == float(frame_width)
    assert y_max == float(frame_height)
    assert x_min >= 0.0
    assert y_min >= 0.0


def test_select_roi_picks_true_argmax_not_first_or_last_cell():
    frame_width, frame_height = 400, 200
    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
    risk_grid = np.full((grid.rows, grid.cols), 5.0)
    target_row, target_col = 1, 3
    risk_grid[target_row, target_col] = 99.0

    x_min, y_min, x_max, y_max = select_roi(risk_grid, grid, frame_width, frame_height)
    center_x, center_y = grid.cell_center(target_row, target_col)

    assert (x_min + x_max) / 2.0 == pytest.approx(center_x)
    assert (y_min + y_max) / 2.0 == pytest.approx(center_y)


def test_select_roi_shape_mismatch_raises():
    grid = CrowdGrid.from_frame_dimensions(400, 200)
    wrong_shape_grid = np.zeros((grid.rows + 1, grid.cols))
    with pytest.raises(ValueError):
        select_roi(wrong_shape_grid, grid, 400, 200)
