"""Shared spatial grid specification for Density and Flow (Phase 9).

Density and Flow must be computed on the IDENTICAL grid (same rows/cols,
same cell boundaries) so Crowd Pressure can combine them via direct
pointwise multiplication without interpolation — this module is the single
source of truth for that grid.
"""

from dataclasses import dataclass

import numpy as np

from app.core.config import settings


@dataclass
class CrowdGrid:
    rows: int
    cols: int
    cell_width_px: float
    cell_height_px: float
    frame_width: int
    frame_height: int

    @classmethod
    def from_frame_dimensions(cls, width: int, height: int) -> "CrowdGrid":
        """Derives rows/cols from frame dimensions and
        settings.CROWD_GRID_CELL_SIZE_PX. Rows/cols are chosen (via
        rounding, not floor/ceil) so that dividing the frame evenly by
        that count keeps actual cell size close to the nominal
        CROWD_GRID_CELL_SIZE_PX while still exactly tiling the whole
        frame with no leftover partial strip at the edges."""
        cell_size_px = settings.CROWD_GRID_CELL_SIZE_PX
        rows = max(1, round(height / cell_size_px))
        cols = max(1, round(width / cell_size_px))
        return cls(
            rows=rows,
            cols=cols,
            cell_width_px=width / cols,
            cell_height_px=height / rows,
            frame_width=width,
            frame_height=height,
        )

    def cell_center(self, row: int, col: int) -> tuple[float, float]:
        x = (col + 0.5) * self.cell_width_px
        y = (row + 0.5) * self.cell_height_px
        return x, y

    def cell_bounds(self, row: int, col: int) -> tuple[float, float, float, float]:
        """(x_min, y_min, x_max, y_max) pixel-space bounding box of a cell."""
        x_min = col * self.cell_width_px
        x_max = (col + 1) * self.cell_width_px
        y_min = row * self.cell_height_px
        y_max = (row + 1) * self.cell_height_px
        return x_min, y_min, x_max, y_max

    def cell_center_grid(self) -> tuple[np.ndarray, np.ndarray]:
        """Vectorized form of cell_center() for every cell at once.
        Returns (xx, yy), each shape (rows, cols) — used for batch KDE
        evaluation."""
        col_centers = (np.arange(self.cols) + 0.5) * self.cell_width_px
        row_centers = (np.arange(self.rows) + 0.5) * self.cell_height_px
        xx, yy = np.meshgrid(col_centers, row_centers)
        return xx, yy

    def cell_index_for_point(self, x: float, y: float) -> tuple[int, int]:
        """(row, col) of the cell containing (x, y), clamped to grid
        bounds (a point exactly on frame_width/frame_height would
        otherwise index one cell past the end)."""
        col = int(x // self.cell_width_px)
        row = int(y // self.cell_height_px)
        col = min(max(col, 0), self.cols - 1)
        row = min(max(row, 0), self.rows - 1)
        return row, col
