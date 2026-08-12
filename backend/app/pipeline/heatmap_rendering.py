"""Heatmap rendering (master spec §12/§13/§40, roadmap Phase 12) — PURE
numpy/OpenCV functions that render ALREADY-COMPUTED CrowdMetrics fields
(Phases 9-11) into images. No file writes, no DB access — see
heatmap_service.py for the I/O layer that calls these. Exactly 5 mandatory
heatmap types (frozen decision, §13/§40): Density, Pressure, Flow/
Congestion (ONE combined type), Risk, Predictive.

============================================================
RENDERING CHOICES (decision #1/#2, fixed code-level constants, NOT new env
vars — over-configuring cosmetic rendering details was deliberately
avoided)
============================================================
- PURE colormap visualizations, no compositing onto the real video frame
  (decision #1) — compositing/opacity is a PRESENTATION concern for a
  future dashboard, not baked in at generation time.
- Upscaled from the coarser CrowdGrid to frame_width x frame_height via
  cv2.resize(..., interpolation=cv2.INTER_LINEAR).
- cv2.COLORMAP_TURBO used CONSISTENTLY across every "danger-scale" type
  (Density, Pressure, Risk, Predictive) — one learnable visual language.
  Flow/Congestion's directional arrows are drawn in a neutral (white) color
  over a TURBO-colormapped congestion-score base layer.

============================================================
RESOLUTION 1 — Risk heatmap is a PER-CELL REAPPLICATION of Phase 11's
risk_score formula, not the same number spatialized
============================================================
Phase 11's `risk_score.py` computes ONE scalar per frame using per-signal
REDUCTIONS (max_pressure, a single congested_cell_fraction, the grid's
minimum bottleneck ratio, a single reverse_flow_cell_fraction) — correct
for its purpose (the future Trigger Engine needs one thresholdable number).
This module instead applies the EXACT SAME weighted-combination formula
and EXACT SAME configured weights (RISK_SCORE_WEIGHT_*, and even the same
`_redistribute_weights` helper — imported directly from risk_score.py, not
reimplemented) POINTWISE, using each signal's own native per-cell field:
  - pressure:      CrowdPressureField.grid[cell] / PRESSURE_SCORE_REFERENCE_PX * 100
  - congestion:     CongestionField.congestion_score_grid[cell] * 100
  - bottleneck:     (1 - BottleneckField.bottleneck_score_grid[cell]) * 100
  - reverse_flow:   ReverseFlowField.is_reverse_flow_grid[cell] ? 100 : 0
This is philosophically CONSISTENT with (same weights, same signals) but
NOT NUMERICALLY IDENTICAL to Phase 11's scalar risk_score — different
reduction method per signal (max/min/fraction reductions vs. direct
pointwise combination). test_heatmap_rendering.py checks a REASONABLE
DIRECTIONAL correlation between this grid's mean/max and the scalar score,
never exact equality (exact equality is not expected and is not the bar).

Bottleneck availability is handled exactly like Phase 11 (weight
redistribution when `BottleneckField` is None for the whole frame) — AND
one level more granularly: if BottleneckField is present but a SPECIFIC
cell's own ratio is NaN (only possible on a degenerate 1-row/1-col grid;
see bottleneck.py's `_spread_grid`), that cell falls back to the
without-bottleneck combination too, rather than propagating NaN into the
rendered image.

============================================================
RESOLUTION 2 — Predictive heatmap is a TREND-SCALED VIEW of the CURRENT
spatial pattern, explicitly NOT an independent per-cell forecast
============================================================
§12 states heatmaps render "already-computed fields," not compute new
ones — genuine per-cell time-series forecasting would be computing NEW
data, outside this phase's charter. Instead: the CURRENT
CrowdPressureField.grid is uniformly SCALED by the ratio
(projection.projected_pressure / current mean_pressure). If current
mean_pressure is 0.0 (an empty/still scene — no spatial pattern exists to
scale), scaling is skipped entirely and the projected value is used
directly as a uniform low-level field instead of dividing by zero. This
limitation is embedded as VISIBLE TEXT directly on the rendered image
(decision #6), not just documented in code — this is the first phase
where the artifact IS the deliverable, not just debug console output.
"""

import math

import cv2
import numpy as np

from app.core.config import settings
from app.pipeline.bottleneck import BottleneckField
from app.pipeline.congestion import CongestionField
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import DensityField
from app.pipeline.flow_field import FlowGridField
from app.pipeline.predictive_projection import PredictiveProjection
from app.pipeline.reverse_flow import ReverseFlowField
from app.pipeline.risk_score import _redistribute_weights, compute_risk_grid  # deliberate reuse, see module docstring
# NOTE: `_redistribute_weights` is no longer called directly in this module
# (Phase 14 extracted the per-cell combination logic below into
# `compute_risk_grid`, which now calls it instead) — kept imported here
# anyway so `test_risk_heatmap_reuses_phase11_redistribute_weights_function_
# directly`'s identity check (`heatmap_rendering._redistribute_weights is
# risk_score._redistribute_weights`) continues to hold, and so a reader of
# this module can still see the connection at a glance.

_COLORMAP = cv2.COLORMAP_TURBO
_RESIZE_INTERPOLATION = cv2.INTER_LINEAR

_DISCLAIMER_FONT = cv2.FONT_HERSHEY_SIMPLEX
_DISCLAIMER_FONT_SCALE = 0.45
_DISCLAIMER_COLOR_BGR = (255, 255, 255)
_DISCLAIMER_THICKNESS = 1
_DISCLAIMER_MARGIN_PX = 10

PRESSURE_UNITS_DISCLAIMER_TEXT = "PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS"
PREDICTIVE_TREND_DISCLAIMER_TEXT = "TREND-SCALED VIEW - NOT AN INDEPENDENT FORECAST"

# Flow/Congestion arrow rendering (cosmetic, fixed, not configurable):
# 252 cells (this project's typical CrowdGrid size) is visually dense for
# one arrow per cell, so every other cell in each dimension is sampled —
# still represents the whole grid's spatial extent, just less cluttered.
_ARROW_SAMPLE_STRIDE = 2
_ARROW_COLOR_BGR = (255, 255, 255)
_ARROW_THICKNESS = 1
_ARROW_MIN_SPEED_PX_PER_SEC = 1.0  # numerical noise floor, not a tunable threshold
_ARROW_LENGTH_SCALE = 0.05  # display px per (px/s) of speed
_ARROW_MAX_LENGTH_FRACTION = 0.4  # of min(cell_width_px, cell_height_px)


def _normalize_and_colormap(
    value_grid: np.ndarray, reference_max: float, frame_width: int, frame_height: int
) -> np.ndarray:
    """Shared core of every render_* function: clip to [0, reference_max],
    scale to [0, 255], upscale to frame dimensions, apply the shared TURBO
    colormap. Returns a (frame_height, frame_width, 3) BGR uint8 image."""
    if reference_max <= 0:
        raise ValueError(f"reference_max must be > 0, got {reference_max}")

    normalized = np.clip(value_grid / reference_max, 0.0, 1.0)
    normalized_u8 = (normalized * 255.0).astype(np.uint8)
    upscaled = cv2.resize(
        normalized_u8, (frame_width, frame_height), interpolation=_RESIZE_INTERPOLATION
    )
    return cv2.applyColorMap(upscaled, _COLORMAP)


def _embed_disclaimer(image: np.ndarray, text: str) -> np.ndarray:
    """Draws `text` small and legible in the bottom-left corner (decision
    #6) — the same disclaimer discipline already applied to Phase 9's
    console output, now extended to the actual persisted artifact."""
    height = image.shape[0]
    position = (_DISCLAIMER_MARGIN_PX, height - _DISCLAIMER_MARGIN_PX)
    cv2.putText(
        image, text, position, _DISCLAIMER_FONT, _DISCLAIMER_FONT_SCALE,
        _DISCLAIMER_COLOR_BGR, _DISCLAIMER_THICKNESS, cv2.LINE_AA,
    )
    return image


def render_density_heatmap(
    density: DensityField, frame_width: int, frame_height: int
) -> np.ndarray:
    return _normalize_and_colormap(
        density.grid, settings.DENSITY_HEATMAP_REFERENCE_COUNT, frame_width, frame_height
    )


def render_pressure_heatmap(
    pressure: CrowdPressureField, frame_width: int, frame_height: int
) -> np.ndarray:
    image = _normalize_and_colormap(
        pressure.grid, settings.PRESSURE_SCORE_REFERENCE_PX, frame_width, frame_height
    )
    return _embed_disclaimer(image, PRESSURE_UNITS_DISCLAIMER_TEXT)


def render_flow_congestion_heatmap(
    congestion: CongestionField, flow: FlowGridField, frame_width: int, frame_height: int
) -> np.ndarray:
    if congestion.congestion_score_grid.shape != flow.grid_mean_velocity.shape[:2]:
        raise ValueError(
            "CongestionField and FlowGridField must share the same grid "
            f"shape; got {congestion.congestion_score_grid.shape} vs "
            f"{flow.grid_mean_velocity.shape[:2]}"
        )

    # Congestion score is already 0-1 native (Phase 10) — reference_max=1.0.
    image = _normalize_and_colormap(
        congestion.congestion_score_grid, 1.0, frame_width, frame_height
    )

    grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)
    for row in range(0, grid.rows, _ARROW_SAMPLE_STRIDE):
        for col in range(0, grid.cols, _ARROW_SAMPLE_STRIDE):
            vx, vy = flow.grid_mean_velocity[row, col]
            speed = math.hypot(float(vx), float(vy))
            if speed < _ARROW_MIN_SPEED_PX_PER_SEC:
                # No meaningful direction to show this cell — skip rather
                # than draw a fabricated/near-zero-length arrow.
                continue

            direction_x, direction_y = vx / speed, vy / speed
            max_length = min(grid.cell_width_px, grid.cell_height_px) * _ARROW_MAX_LENGTH_FRACTION
            length = min(speed * _ARROW_LENGTH_SCALE, max_length)

            cx, cy = grid.cell_center(row, col)
            start_point = (int(cx), int(cy))
            end_point = (int(cx + direction_x * length), int(cy + direction_y * length))
            cv2.arrowedLine(
                image, start_point, end_point, _ARROW_COLOR_BGR, _ARROW_THICKNESS,
                cv2.LINE_AA, tipLength=0.35,
            )

    return image


def render_risk_heatmap(
    pressure: CrowdPressureField,
    congestion: CongestionField,
    bottleneck: BottleneckField | None,
    reverse_flow: ReverseFlowField,
    frame_width: int,
    frame_height: int,
) -> np.ndarray:
    # Phase 14 (decision #1): the per-cell combination formula itself now
    # lives in risk_score.py's compute_risk_grid (extracted from here, this
    # module's own original Resolution 1) so this module and
    # roi_selection.py's select_roi share ONE implementation. Behavior is
    # unchanged — see compute_risk_grid's docstring for the full formula.
    risk_grid = compute_risk_grid(pressure, congestion, bottleneck, reverse_flow)
    return _normalize_and_colormap(risk_grid, 100.0, frame_width, frame_height)


def render_predictive_heatmap(
    pressure: CrowdPressureField,
    projection: PredictiveProjection,
    frame_width: int,
    frame_height: int,
) -> np.ndarray:
    if pressure.mean_pressure > 0:
        scale_ratio = projection.projected_pressure / pressure.mean_pressure
        scaled_grid = pressure.grid * scale_ratio
    else:
        # Zero-division guard (Resolution 2's documented edge case): no
        # spatial pattern exists in an empty/still scene to scale — use the
        # projected value directly as a uniform low-level field instead.
        scaled_grid = np.full_like(pressure.grid, projection.projected_pressure)

    image = _normalize_and_colormap(
        scaled_grid, settings.PRESSURE_SCORE_REFERENCE_PX, frame_width, frame_height
    )
    return _embed_disclaimer(image, PREDICTIVE_TREND_DISCLAIMER_TEXT)
