import numpy as np
import pytest

from app.pipeline.crowd_pressure import compute_crowd_pressure_field
from app.pipeline.density import DensityField
from app.pipeline.flow_field import FlowGridField


def _density_field(grid: np.ndarray) -> DensityField:
    return DensityField(
        frame_number=5,
        timestamp_seconds=5 / 30,
        grid=grid,
        track_count=int(grid.sum()),
        estimation_confidence=1.0,
        degradation_reason=None,
        voronoi_disagreement_summary=0.1,
    )


def _flow_field(velocity_variance: np.ndarray) -> FlowGridField:
    rows, cols = velocity_variance.shape
    return FlowGridField(
        frame_number=5,
        timestamp_seconds=5 / 30,
        grid_mean_velocity=np.zeros((rows, cols, 2)),
        grid_velocity_variance=velocity_variance,
        grid_divergence=np.zeros((rows, cols)),
        grid_curl=np.zeros((rows, cols)),
        source_motion_frame_number=5,
    )


def test_pressure_grid_matches_hand_calculated_pointwise_product():
    density_grid = np.array([[1.0, 2.0], [0.0, 4.0]])
    variance_grid = np.array([[10.0, 5.0], [100.0, 0.5]])
    expected = np.array([[10.0, 10.0], [0.0, 2.0]])

    result = compute_crowd_pressure_field(
        _density_field(density_grid), _flow_field(variance_grid)
    )

    np.testing.assert_allclose(result.grid, expected)


def test_max_and_mean_pressure_correctly_summarize_grid():
    density_grid = np.array([[1.0, 2.0], [0.0, 4.0]])
    variance_grid = np.array([[10.0, 5.0], [100.0, 0.5]])
    expected = density_grid * variance_grid

    result = compute_crowd_pressure_field(
        _density_field(density_grid), _flow_field(variance_grid)
    )

    assert result.max_pressure == pytest.approx(expected.max())
    assert result.mean_pressure == pytest.approx(expected.mean())


def test_mismatched_grid_shapes_raise_clear_error():
    density_grid = np.zeros((3, 3))
    variance_grid = np.zeros((4, 4))

    with pytest.raises(ValueError):
        compute_crowd_pressure_field(_density_field(density_grid), _flow_field(variance_grid))


def test_units_disclaimer_present_and_nonempty():
    density_grid = np.array([[1.0]])
    variance_grid = np.array([[1.0]])

    result = compute_crowd_pressure_field(
        _density_field(density_grid), _flow_field(variance_grid)
    )

    assert isinstance(result.units_disclaimer, str)
    assert len(result.units_disclaimer) > 0
    assert "pixel" in result.units_disclaimer.lower()
