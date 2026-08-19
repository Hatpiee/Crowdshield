"""Heatmap rendering (master spec §12/§13/§40, roadmap Phase 12) — PURE
numpy/OpenCV functions that render ALREADY-COMPUTED CrowdMetrics fields
(Phases 9-11) into images. No file writes, no DB access — see
heatmap_service.py for the I/O layer that calls these. Exactly 5 mandatory
heatmap types (frozen decision, §13/§40): Density, Pressure, Flow/
Congestion (ONE combined type), Risk, Predictive.

============================================================
HEATMAP RENDERING REWRITE (see DECISIONS.md, "Heatmap Rendering Rewrite")
============================================================
The prior phase's rendering was mathematically correct but operator-unusable
(developer's own real testing): every "danger-scale" type shared the
identical TURBO colormap, there was no source-scene context (a flat
colormap block alone is often impossible to localize spatially), and there
was no legend/units/timestamp/type label anywhere in the artifact itself —
only two of five types had even a tiny burned-in disclaimer string. This
rewrite changes ONLY the rendering/visualization layer — every underlying
numerical field (density.grid, pressure.grid, compute_risk_grid, etc.) is
completely unchanged; this module still renders ALREADY-COMPUTED fields, it
does not compute new ones.

Changes made:
1. `cv2.INTER_LINEAR` -> `cv2.INTER_CUBIC` for the grid->frame upscale
   (visualization-only, smoother-looking field at this upscale factor).
2. Distinct colormap per type, so each metric is visually distinguishable
   at a glance rather than "everything looks the same":
   - Density: `cv2.COLORMAP_INFERNO` (dark-to-hot intensity).
   - Pressure: kept `cv2.COLORMAP_TURBO` (already established; its
     pixel-space disclaimer is unchanged).
   - Risk: a NEW custom green(calm)->amber->red-orange(critical) LUT
     (`_build_risk_colormap`) matching this app's own teal/amber design
     tokens — OpenCV has no built-in calm-to-warning diverging colormap.
   - Predictive: kept TURBO deliberately — it IS a scaled view of Pressure
     (Resolution 2), so sharing Pressure's colormap is semantically honest,
     not an oversight.
   - Flow/Congestion: unchanged neutral TURBO base + white arrows — the
     arrows already carry the primary information, not color.
3. Source-frame overlay: each colormap layer is composited over the REAL
   source video frame via `cv2.addWeighted` (`_composite_over_source_frame`)
   at `settings.HEATMAP_OVERLAY_ALPHA` opacity — replacing the prior
   phase's deliberate "no compositing at all" decision (this module's own
   former decision #1, now superseded and documented as such rather than
   silently reversed).
4. A real legend (gradient bar + min/max + units), plus a timestamp + type
   label, burned directly into every artifact (`_draw_legend_and_labels`)
   — extends the ALREADY-ESTABLISHED "artifact IS the deliverable, legend
   lives in the pixels" pattern from the prior phase's Pressure/Predictive
   disclaimers, rather than adding a parallel structured metadata API
   (`HeatmapSnapshot` gets no new DB columns for this — a deliberate
   simplicity choice, with the honest tradeoff that this is not machine-
   readable, acceptable for this operator-dashboard MVP scope, same class
   of tradeoff already accepted for the prior disclaimers).
5. JPEG quality raised 90->95 in heatmap_service.py (format itself stays
   JPEG, not PNG — investigated switching per the request's instruction,
   but rejected: these are now composited PHOTOGRAPHIC frames, not flat
   colormap blocks, so JPEG compresses this content meaningfully better
   than PNG would; the image-serving route/media-token mechanism is
   already format-agnostic either way, so this is a considered-and-
   rejected alternative, not an unexamined default).

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
reduction method per signal. test_heatmap_rendering.py checks a REASONABLE
DIRECTIONAL correlation between this grid's mean/max and the scalar score,
never exact equality (exact equality is not expected and is not the bar).
The rendered artifact's own burned-in label now says this explicitly too
(the Heatmap Rendering Rewrite's decision 4 above), not just in code
comments — per the request's "do not imply it is numerically identical"
instruction made visible on the artifact itself.

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

_RESIZE_INTERPOLATION = cv2.INTER_CUBIC

_DISCLAIMER_FONT = cv2.FONT_HERSHEY_SIMPLEX
_DISCLAIMER_FONT_SCALE = 0.45
_DISCLAIMER_COLOR_BGR = (255, 255, 255)
_DISCLAIMER_THICKNESS = 1
_DISCLAIMER_MARGIN_PX = 10

PRESSURE_UNITS_DISCLAIMER_TEXT = "PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS"
PREDICTIVE_TREND_DISCLAIMER_TEXT = "TREND-SCALED VIEW - NOT AN INDEPENDENT FORECAST"
RISK_SPATIAL_DISCLAIMER_TEXT = "SPATIAL RISK - SEE SESSION RISK SCORE FOR FRAME-LEVEL VALUE"

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

# Legend gradient bar geometry (cosmetic, fixed, not configurable — same
# category as the arrow-rendering constants above).
_LEGEND_BAR_WIDTH_PX = 160
_LEGEND_BAR_HEIGHT_PX = 14
_LEGEND_MARGIN_PX = 10
_LEGEND_LABEL_FONT_SCALE = 0.4


def _build_risk_colormap() -> np.ndarray:
    """Heatmap Rendering Rewrite: a custom green(calm)->amber->red-orange
    (critical) diverging LUT for the Risk heatmap specifically, matching
    this app's own teal/amber design tokens (cs-teal #2dd4bf, amber
    #F2A93B/#FF6B35) rather than reusing a generic stock colormap for every
    metric. OpenCV has no built-in calm-to-warning diverging colormap, so
    this is hand-authored via linear RGB interpolation through three
    control stops, then converted to BGR (OpenCV's native channel order)
    for `cv2.applyColorMap`'s custom-userColor overload (a (256, 1, 3)
    array, used identically to a stock `cv2.COLORMAP_*` int constant)."""
    stop_positions = np.array([0.0, 0.5, 1.0])
    stop_colors_rgb = np.array(
        [
            (45, 212, 191),  # cs-teal — calm / low risk
            (242, 169, 59),  # amber — elevated
            (255, 75, 53),  # hot red-orange — critical
        ],
        dtype=np.float64,
    )
    xs = np.linspace(0.0, 1.0, 256)
    lut_rgb = np.stack(
        [np.interp(xs, stop_positions, stop_colors_rgb[:, channel]) for channel in range(3)],
        axis=-1,
    ).astype(np.uint8)
    return lut_rgb[:, ::-1].reshape(256, 1, 3)  # RGB -> BGR, shaped for applyColorMap


_DENSITY_COLORMAP = cv2.COLORMAP_INFERNO
_PRESSURE_COLORMAP = cv2.COLORMAP_TURBO
_PREDICTIVE_COLORMAP = cv2.COLORMAP_TURBO
_FLOW_CONGESTION_COLORMAP = cv2.COLORMAP_TURBO
_RISK_COLORMAP = _build_risk_colormap()


def _normalize_and_colormap(
    value_grid: np.ndarray, reference_max: float, frame_width: int, frame_height: int, colormap
) -> np.ndarray:
    """Shared core of every render_* function: clip to [0, reference_max],
    scale to [0, 255], upscale to frame dimensions, apply the given
    colormap (either a stock `cv2.COLORMAP_*` int, or a custom (256, 1, 3)
    LUT array — see `_build_risk_colormap`). Returns a
    (frame_height, frame_width, 3) BGR uint8 image."""
    if reference_max <= 0:
        raise ValueError(f"reference_max must be > 0, got {reference_max}")

    normalized = np.clip(value_grid / reference_max, 0.0, 1.0)
    normalized_u8 = (normalized * 255.0).astype(np.uint8)
    upscaled = cv2.resize(
        normalized_u8, (frame_width, frame_height), interpolation=_RESIZE_INTERPOLATION
    )
    return cv2.applyColorMap(upscaled, colormap)


def _composite_over_source_frame(colormap_image: np.ndarray, source_frame: np.ndarray) -> np.ndarray:
    """Heatmap Rendering Rewrite: composites the colormap layer over the
    REAL source video frame at `settings.HEATMAP_OVERLAY_ALPHA` opacity —
    an operator can now see WHERE a colored region sits relative to actual
    people/objects/the event itself, not just an abstract colored blob.
    Does NOT alter the original video frame array itself (a fresh weighted-
    sum image is produced) — same "derived visualization layer, source
    untouched" discipline as everything else in this module."""
    alpha = settings.HEATMAP_OVERLAY_ALPHA
    return cv2.addWeighted(colormap_image, alpha, source_frame, 1.0 - alpha, 0.0)


def _legend_gradient_strip(colormap, bar_width_px: int) -> np.ndarray:
    gradient_row = np.linspace(0, 255, bar_width_px).astype(np.uint8).reshape(1, -1)
    gradient_image = cv2.applyColorMap(gradient_row, colormap)
    return cv2.resize(
        gradient_image, (bar_width_px, _LEGEND_BAR_HEIGHT_PX), interpolation=cv2.INTER_NEAREST
    )


def _draw_legend_and_labels(
    image: np.ndarray,
    colormap,
    value_min: float,
    value_max: float,
    units: str,
    heatmap_type_label: str,
    timestamp_seconds: float,
    extra_disclaimer: str | None = None,
) -> np.ndarray:
    """Heatmap Rendering Rewrite: burns a real legend (gradient bar +
    min/max + units), plus a timestamp + heatmap-type label, directly into
    the artifact — see module docstring's decision 4 for why this lives in
    the pixels rather than a parallel metadata API.

    REAL BUG CAUGHT WHILE TESTING THIS: the legend bar's width was
    originally a fixed constant (_LEGEND_BAR_WIDTH_PX) regardless of the
    actual frame size — on a small video (e.g. this project's own 64x64
    synthetic test fixture), a 160px-wide bar starting at x=10 doesn't fit
    inside a 64px-wide frame at all, and numpy's slice-assignment silently
    clips the DESTINATION region while the SOURCE strip stays full width,
    raising a broadcast ValueError. Caught by
    test_analysis_orchestrator.py's real full-run tests (which use exactly
    that small synthetic fixture) actually failing with COMPLETED runs
    turning into FAILED ones. Fixed by clamping the bar width to whatever
    actually fits within the image, never assuming a frame is large enough
    for a fixed-pixel-size overlay."""
    height, width = image.shape[:2]

    cv2.putText(
        image, f"{heatmap_type_label}  t={timestamp_seconds:.2f}s",
        (_LEGEND_MARGIN_PX, 20), _DISCLAIMER_FONT, _DISCLAIMER_FONT_SCALE,
        _DISCLAIMER_COLOR_BGR, _DISCLAIMER_THICKNESS, cv2.LINE_AA,
    )

    bar_width_px = max(1, min(_LEGEND_BAR_WIDTH_PX, width - 2 * _LEGEND_MARGIN_PX))
    disclaimer_reserved_px = 16 if extra_disclaimer else 0
    bar_y = height - _LEGEND_MARGIN_PX - _LEGEND_BAR_HEIGHT_PX - disclaimer_reserved_px
    bar_x = _LEGEND_MARGIN_PX
    strip = _legend_gradient_strip(colormap, bar_width_px)
    image[bar_y : bar_y + _LEGEND_BAR_HEIGHT_PX, bar_x : bar_x + bar_width_px] = strip

    cv2.putText(
        image, f"{value_min:.0f}", (bar_x, bar_y - 4), _DISCLAIMER_FONT,
        _LEGEND_LABEL_FONT_SCALE, _DISCLAIMER_COLOR_BGR, 1, cv2.LINE_AA,
    )
    max_label = f"{value_max:.0f} {units}".strip()
    (text_width, _), _ = cv2.getTextSize(max_label, _DISCLAIMER_FONT, _LEGEND_LABEL_FONT_SCALE, 1)
    max_label_x = max(bar_x, bar_x + bar_width_px - text_width)
    cv2.putText(
        image, max_label, (max_label_x, bar_y - 4),
        _DISCLAIMER_FONT, _LEGEND_LABEL_FONT_SCALE, _DISCLAIMER_COLOR_BGR, 1, cv2.LINE_AA,
    )

    if extra_disclaimer:
        cv2.putText(
            image, extra_disclaimer, (_LEGEND_MARGIN_PX, height - _DISCLAIMER_MARGIN_PX),
            _DISCLAIMER_FONT, _DISCLAIMER_FONT_SCALE, _DISCLAIMER_COLOR_BGR,
            _DISCLAIMER_THICKNESS, cv2.LINE_AA,
        )

    return image


def render_density_heatmap(
    density: DensityField,
    frame_width: int,
    frame_height: int,
    source_frame: np.ndarray,
    timestamp_seconds: float,
) -> np.ndarray:
    colormap_image = _normalize_and_colormap(
        density.grid, settings.DENSITY_HEATMAP_REFERENCE_COUNT, frame_width, frame_height,
        _DENSITY_COLORMAP,
    )
    image = _composite_over_source_frame(colormap_image, source_frame)
    return _draw_legend_and_labels(
        image, _DENSITY_COLORMAP, 0.0, settings.DENSITY_HEATMAP_REFERENCE_COUNT,
        "people/cell", "DENSITY", timestamp_seconds,
    )


def render_pressure_heatmap(
    pressure: CrowdPressureField,
    frame_width: int,
    frame_height: int,
    source_frame: np.ndarray,
    timestamp_seconds: float,
) -> np.ndarray:
    colormap_image = _normalize_and_colormap(
        pressure.grid, settings.PRESSURE_SCORE_REFERENCE_PX, frame_width, frame_height,
        _PRESSURE_COLORMAP,
    )
    image = _composite_over_source_frame(colormap_image, source_frame)
    return _draw_legend_and_labels(
        image, _PRESSURE_COLORMAP, 0.0, settings.PRESSURE_SCORE_REFERENCE_PX,
        "px-space", "PRESSURE", timestamp_seconds,
        extra_disclaimer=PRESSURE_UNITS_DISCLAIMER_TEXT,
    )


def render_flow_congestion_heatmap(
    congestion: CongestionField,
    flow: FlowGridField,
    frame_width: int,
    frame_height: int,
    source_frame: np.ndarray,
    timestamp_seconds: float,
) -> np.ndarray:
    if congestion.congestion_score_grid.shape != flow.grid_mean_velocity.shape[:2]:
        raise ValueError(
            "CongestionField and FlowGridField must share the same grid "
            f"shape; got {congestion.congestion_score_grid.shape} vs "
            f"{flow.grid_mean_velocity.shape[:2]}"
        )

    # Congestion score is already 0-1 native (Phase 10) — reference_max=1.0.
    colormap_image = _normalize_and_colormap(
        congestion.congestion_score_grid, 1.0, frame_width, frame_height,
        _FLOW_CONGESTION_COLORMAP,
    )
    image = _composite_over_source_frame(colormap_image, source_frame)

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

    return _draw_legend_and_labels(
        image, _FLOW_CONGESTION_COLORMAP, 0.0, 100.0, "% congested cells",
        "FLOW / CONGESTION", timestamp_seconds,
    )


def render_risk_heatmap(
    pressure: CrowdPressureField,
    congestion: CongestionField,
    bottleneck: BottleneckField | None,
    reverse_flow: ReverseFlowField,
    frame_width: int,
    frame_height: int,
    source_frame: np.ndarray,
    timestamp_seconds: float,
) -> np.ndarray:
    # Phase 14 (decision #1): the per-cell combination formula itself now
    # lives in risk_score.py's compute_risk_grid (extracted from here, this
    # module's own original Resolution 1) so this module and
    # roi_selection.py's select_roi share ONE implementation. Behavior is
    # unchanged — see compute_risk_grid's docstring for the full formula.
    risk_grid = compute_risk_grid(pressure, congestion, bottleneck, reverse_flow)
    colormap_image = _normalize_and_colormap(
        risk_grid, 100.0, frame_width, frame_height, _RISK_COLORMAP
    )
    image = _composite_over_source_frame(colormap_image, source_frame)
    return _draw_legend_and_labels(
        image, _RISK_COLORMAP, 0.0, 100.0, "0-100", "RISK", timestamp_seconds,
        extra_disclaimer=RISK_SPATIAL_DISCLAIMER_TEXT,
    )


def render_predictive_heatmap(
    pressure: CrowdPressureField,
    projection: PredictiveProjection,
    frame_width: int,
    frame_height: int,
    source_frame: np.ndarray,
    timestamp_seconds: float,
) -> np.ndarray:
    if pressure.mean_pressure > 0:
        scale_ratio = projection.projected_pressure / pressure.mean_pressure
        scaled_grid = pressure.grid * scale_ratio
    else:
        # Zero-division guard (Resolution 2's documented edge case): no
        # spatial pattern exists in an empty/still scene to scale — use the
        # projected value directly as a uniform low-level field instead.
        scaled_grid = np.full_like(pressure.grid, projection.projected_pressure)

    colormap_image = _normalize_and_colormap(
        scaled_grid, settings.PRESSURE_SCORE_REFERENCE_PX, frame_width, frame_height,
        _PREDICTIVE_COLORMAP,
    )
    image = _composite_over_source_frame(colormap_image, source_frame)
    return _draw_legend_and_labels(
        image, _PREDICTIVE_COLORMAP, 0.0, settings.PRESSURE_SCORE_REFERENCE_PX,
        "px-space, trend-scaled", "PREDICTIVE", timestamp_seconds,
        extra_disclaimer=PREDICTIVE_TREND_DISCLAIMER_TEXT,
    )
