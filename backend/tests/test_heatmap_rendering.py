import cv2
import numpy as np
import pytest

from app.core.config import settings
from app.pipeline import heatmap_rendering, risk_score
from app.pipeline.bottleneck import BottleneckField
from app.pipeline.congestion import CongestionField
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import DensityField
from app.pipeline.flow_field import FlowGridField
from app.pipeline.heatmap_rendering import (
    PREDICTIVE_TREND_DISCLAIMER_TEXT,
    PRESSURE_UNITS_DISCLAIMER_TEXT,
    _embed_disclaimer,
    _normalize_and_colormap,
    render_density_heatmap,
    render_flow_congestion_heatmap,
    render_predictive_heatmap,
    render_pressure_heatmap,
    render_risk_heatmap,
)
from app.pipeline.predictive_projection import PredictiveProjection
from app.pipeline.reverse_flow import ReverseFlowField
from app.pipeline.risk_score import compute_risk_score

# Matches test_flow_field.py's convention (320x240 @ CROWD_GRID_CELL_SIZE_PX=40
# -> rows=6, cols=8) so render_flow_congestion_heatmap's internal
# CrowdGrid.from_frame_dimensions() reconstruction lines up with hand-built
# grids of this exact shape.
FRAME_WIDTH = 320
FRAME_HEIGHT = 240
ROWS, COLS = 6, 8


def _density(grid: np.ndarray, confidence: float = 1.0) -> DensityField:
    return DensityField(
        frame_number=1,
        timestamp_seconds=1 / 30,
        grid=grid,
        track_count=int(grid.sum()),
        estimation_confidence=confidence,
        degradation_reason=None,
        voronoi_disagreement_summary=None,
    )


def _pressure(grid: np.ndarray, mean_pressure: float | None = None) -> CrowdPressureField:
    return CrowdPressureField(
        frame_number=1,
        timestamp_seconds=1 / 30,
        grid=grid,
        max_pressure=float(grid.max()),
        mean_pressure=float(grid.mean()) if mean_pressure is None else mean_pressure,
    )


def _congestion(score_grid: np.ndarray) -> CongestionField:
    return CongestionField(
        frame_number=1,
        timestamp_seconds=1 / 30,
        is_congested_grid=score_grid > 0.5,
        congestion_score_grid=score_grid,
        congested_cell_fraction=float((score_grid > 0.5).mean()),
    )


def _flow(velocity: np.ndarray) -> FlowGridField:
    rows, cols = velocity.shape[:2]
    return FlowGridField(
        frame_number=1,
        timestamp_seconds=1 / 30,
        grid_mean_velocity=velocity,
        grid_velocity_variance=np.zeros((rows, cols)),
        grid_divergence=np.zeros((rows, cols)),
        grid_curl=np.zeros((rows, cols)),
        source_motion_frame_number=1,
    )


def _bottleneck(score_grid: np.ndarray) -> BottleneckField:
    return BottleneckField(
        frame_number=1,
        timestamp_seconds=1 / 30,
        window_frames_used=30,
        bottleneck_score_grid=score_grid,
        strongest_bottleneck_cell=None,
    )


def _reverse_flow(reversed_grid: np.ndarray) -> ReverseFlowField:
    return ReverseFlowField(
        frame_number=1,
        timestamp_seconds=1 / 30,
        is_reverse_flow_grid=reversed_grid,
        reverse_flow_cell_fraction=float(reversed_grid.mean()),
        cells_with_established_baseline=int(reversed_grid.size),
    )


def _projection(projected_pressure: float, r_squared: float = 0.9) -> PredictiveProjection:
    return PredictiveProjection(
        frame_number=1,
        timestamp_seconds=1 / 30,
        horizon_seconds=settings.PREDICTION_HORIZON_SECONDS,
        projected_pressure=projected_pressure,
        r_squared=r_squared,
        window_seconds_used=5.0,
        data_points_used=10,
    )


def _top_and_bottom_colors() -> tuple[np.ndarray, np.ndarray]:
    top = cv2.applyColorMap(np.array([[255]], dtype=np.uint8), cv2.COLORMAP_TURBO)[0, 0]
    bottom = cv2.applyColorMap(np.array([[0]], dtype=np.uint8), cv2.COLORMAP_TURBO)[0, 0]
    return top, bottom


# ---------------------------------------------------------------------------
# Dimension checks (all 5 types)
# ---------------------------------------------------------------------------


def test_density_heatmap_has_correct_dimensions():
    grid = np.zeros((ROWS, COLS))
    image = render_density_heatmap(_density(grid), FRAME_WIDTH, FRAME_HEIGHT)
    assert image.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)


def test_pressure_heatmap_has_correct_dimensions():
    grid = np.zeros((ROWS, COLS))
    image = render_pressure_heatmap(_pressure(grid), FRAME_WIDTH, FRAME_HEIGHT)
    assert image.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)


def test_flow_congestion_heatmap_has_correct_dimensions():
    congestion = _congestion(np.zeros((ROWS, COLS)))
    flow = _flow(np.zeros((ROWS, COLS, 2)))
    image = render_flow_congestion_heatmap(congestion, flow, FRAME_WIDTH, FRAME_HEIGHT)
    assert image.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)


def test_risk_heatmap_has_correct_dimensions():
    pressure = _pressure(np.zeros((ROWS, COLS)))
    congestion = _congestion(np.zeros((ROWS, COLS)))
    bottleneck = _bottleneck(np.ones((ROWS, COLS)))
    reverse_flow = _reverse_flow(np.zeros((ROWS, COLS), dtype=bool))
    image = render_risk_heatmap(pressure, congestion, bottleneck, reverse_flow, FRAME_WIDTH, FRAME_HEIGHT)
    assert image.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)


def test_predictive_heatmap_has_correct_dimensions():
    pressure = _pressure(np.ones((ROWS, COLS)) * 10.0)
    projection = _projection(projected_pressure=10.0)
    image = render_predictive_heatmap(pressure, projection, FRAME_WIDTH, FRAME_HEIGHT)
    assert image.shape == (FRAME_HEIGHT, FRAME_WIDTH, 3)


# ---------------------------------------------------------------------------
# Normalization: exactly-at-reference -> top of scale; zero -> bottom of scale
# ---------------------------------------------------------------------------


def test_density_normalization_reference_and_zero(monkeypatch):
    monkeypatch.setattr(settings, "DENSITY_HEATMAP_REFERENCE_COUNT", 0.2)
    top, bottom = _top_and_bottom_colors()

    at_reference = render_density_heatmap(
        _density(np.full((ROWS, COLS), 0.2)), FRAME_WIDTH, FRAME_HEIGHT
    )
    np.testing.assert_array_equal(at_reference[0, 0], top)

    at_zero = render_density_heatmap(_density(np.zeros((ROWS, COLS))), FRAME_WIDTH, FRAME_HEIGHT)
    np.testing.assert_array_equal(at_zero[0, 0], bottom)


def test_pressure_normalization_reference_and_zero(monkeypatch):
    monkeypatch.setattr(settings, "PRESSURE_SCORE_REFERENCE_PX", 100.0)
    top, bottom = _top_and_bottom_colors()

    at_reference = render_pressure_heatmap(
        _pressure(np.full((ROWS, COLS), 100.0)), FRAME_WIDTH, FRAME_HEIGHT
    )
    # Sample top-right, away from the bottom-left disclaimer text.
    np.testing.assert_array_equal(at_reference[0, -1], top)

    at_zero = render_pressure_heatmap(_pressure(np.zeros((ROWS, COLS))), FRAME_WIDTH, FRAME_HEIGHT)
    np.testing.assert_array_equal(at_zero[0, -1], bottom)


def test_risk_normalization_reference_and_zero():
    top, bottom = _top_and_bottom_colors()

    # All four sub-scores driven to exactly 100 -> weighted average is 100.
    pressure = _pressure(np.full((ROWS, COLS), settings.PRESSURE_SCORE_REFERENCE_PX))
    congestion = _congestion(np.ones((ROWS, COLS)))
    bottleneck = _bottleneck(np.zeros((ROWS, COLS)))  # (1-0)*100 = 100
    reverse_flow = _reverse_flow(np.ones((ROWS, COLS), dtype=bool))
    at_reference = render_risk_heatmap(
        pressure, congestion, bottleneck, reverse_flow, FRAME_WIDTH, FRAME_HEIGHT
    )
    np.testing.assert_array_equal(at_reference[0, 0], top)

    # All four sub-scores driven to exactly 0.
    pressure_zero = _pressure(np.zeros((ROWS, COLS)))
    congestion_zero = _congestion(np.zeros((ROWS, COLS)))
    bottleneck_zero = _bottleneck(np.ones((ROWS, COLS)))  # (1-1)*100 = 0
    reverse_flow_zero = _reverse_flow(np.zeros((ROWS, COLS), dtype=bool))
    at_zero = render_risk_heatmap(
        pressure_zero, congestion_zero, bottleneck_zero, reverse_flow_zero, FRAME_WIDTH, FRAME_HEIGHT
    )
    np.testing.assert_array_equal(at_zero[0, 0], bottom)


# ---------------------------------------------------------------------------
# Risk heatmap: reuses Phase 11's exact weights + directional correlation
# ---------------------------------------------------------------------------


def test_risk_heatmap_reuses_phase11_redistribute_weights_function_directly():
    # Not a duplicated/hardcoded reimplementation — the literal same
    # function object imported from risk_score.py.
    assert heatmap_rendering._redistribute_weights is risk_score._redistribute_weights


def test_risk_heatmap_directional_correlation_with_phase11_scalar_not_exact_equality():
    density = _density(np.array([[1.0]]))

    low_pressure = _pressure(np.full((ROWS, COLS), 1.0))
    low_congestion = _congestion(np.full((ROWS, COLS), 0.02))
    low_bottleneck = _bottleneck(np.full((ROWS, COLS), 0.98))
    low_reverse_flow = _reverse_flow(np.zeros((ROWS, COLS), dtype=bool))

    high_pressure = _pressure(np.full((ROWS, COLS), settings.PRESSURE_SCORE_REFERENCE_PX * 0.9))
    high_congestion = _congestion(np.full((ROWS, COLS), 0.9))
    high_bottleneck = _bottleneck(np.full((ROWS, COLS), 0.1))
    high_reverse_flow = _reverse_flow(np.ones((ROWS, COLS), dtype=bool))

    low_scalar = compute_risk_score(
        density, low_pressure, low_congestion, low_bottleneck, low_reverse_flow
    ).risk_score
    high_scalar = compute_risk_score(
        density, high_pressure, high_congestion, high_bottleneck, high_reverse_flow
    ).risk_score
    assert low_scalar < high_scalar

    low_grid = render_risk_heatmap(
        low_pressure, low_congestion, low_bottleneck, low_reverse_flow, FRAME_WIDTH, FRAME_HEIGHT
    ).astype(float)
    high_grid = render_risk_heatmap(
        high_pressure, high_congestion, high_bottleneck, high_reverse_flow, FRAME_WIDTH, FRAME_HEIGHT
    ).astype(float)
    # Directional correlation only (NOT exact equality — different
    # reduction methods per signal, see module docstring's Resolution 1).
    assert low_grid.mean() < high_grid.mean()
    assert low_grid.max() < high_grid.max()


# ---------------------------------------------------------------------------
# Predictive heatmap: exact hand-calculated trend-scaling + zero-division guard
# ---------------------------------------------------------------------------


def test_predictive_heatmap_matches_hand_calculated_trend_scaling():
    grid = np.array(
        [[1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0]] * ROWS
    )  # mean = 4.5
    pressure = _pressure(grid, mean_pressure=4.5)
    projection = _projection(projected_pressure=9.0)  # ratio = 9.0 / 4.5 = 2.0

    expected_scaled_grid = grid * 2.0  # hand-calculated: [2,4,...,16]
    expected_image = _normalize_and_colormap(
        expected_scaled_grid, settings.PRESSURE_SCORE_REFERENCE_PX, FRAME_WIDTH, FRAME_HEIGHT
    )
    expected_image = _embed_disclaimer(expected_image, PREDICTIVE_TREND_DISCLAIMER_TEXT)

    actual_image = render_predictive_heatmap(pressure, projection, FRAME_WIDTH, FRAME_HEIGHT)
    np.testing.assert_array_equal(actual_image, expected_image)


def test_predictive_heatmap_zero_current_mean_pressure_does_not_divide_by_zero():
    grid = np.zeros((ROWS, COLS))
    pressure = _pressure(grid, mean_pressure=0.0)
    projection = _projection(projected_pressure=7.5)

    expected_scaled_grid = np.full_like(grid, 7.5)
    expected_image = _normalize_and_colormap(
        expected_scaled_grid, settings.PRESSURE_SCORE_REFERENCE_PX, FRAME_WIDTH, FRAME_HEIGHT
    )
    expected_image = _embed_disclaimer(expected_image, PREDICTIVE_TREND_DISCLAIMER_TEXT)

    actual_image = render_predictive_heatmap(pressure, projection, FRAME_WIDTH, FRAME_HEIGHT)
    np.testing.assert_array_equal(actual_image, expected_image)


# ---------------------------------------------------------------------------
# Embedded disclaimers genuinely present (not just trusted from code)
# ---------------------------------------------------------------------------


def test_pressure_heatmap_units_disclaimer_genuinely_present():
    # Uniform grid -> without any text, EVERY pixel would be identical.
    pressure = _pressure(np.full((ROWS, COLS), 50.0))
    image = render_pressure_heatmap(pressure, FRAME_WIDTH, FRAME_HEIGHT)

    base_color = image[0, -1]  # top-right, far from the bottom-left text
    bottom_left_region = image[FRAME_HEIGHT - 20 : FRAME_HEIGHT, 0:150]
    # If the disclaimer were NOT drawn, this region would be perfectly
    # uniform (== base_color everywhere); text pixels break that uniformity.
    assert not np.all(bottom_left_region == base_color)
    # And confirm rendering elsewhere was NOT altered by the text call.
    assert np.all(image[0, FRAME_WIDTH - 20 :] == base_color)


def test_predictive_heatmap_trend_disclaimer_genuinely_present():
    pressure = _pressure(np.full((ROWS, COLS), 5.0), mean_pressure=5.0)
    projection = _projection(projected_pressure=5.0)
    image = render_predictive_heatmap(pressure, projection, FRAME_WIDTH, FRAME_HEIGHT)

    base_color = image[0, -1]
    bottom_left_region = image[FRAME_HEIGHT - 20 : FRAME_HEIGHT, 0:150]
    assert not np.all(bottom_left_region == base_color)
