import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.congestion import compute_congestion_field
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


def _flow_field(grid_mean_velocity: np.ndarray) -> FlowGridField:
    rows, cols = grid_mean_velocity.shape[:2]
    return FlowGridField(
        frame_number=5,
        timestamp_seconds=5 / 30,
        grid_mean_velocity=grid_mean_velocity,
        grid_velocity_variance=np.zeros((rows, cols)),
        grid_divergence=np.zeros((rows, cols)),
        grid_curl=np.zeros((rows, cols)),
        source_motion_frame_number=5,
    )


def test_high_density_and_low_flow_is_congested(monkeypatch):
    monkeypatch.setattr(settings, "DENSITY_CONGESTION_THRESHOLD", 1.0)
    monkeypatch.setattr(settings, "FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC", 10.0)

    density_grid = np.array([[5.0]])
    velocity = np.zeros((1, 1, 2))
    velocity[0, 0] = (2.0, 0.0)  # speed=2, well below the 10 px/s threshold

    result = compute_congestion_field(_density_field(density_grid), _flow_field(velocity))

    assert result.is_congested_grid[0, 0] == True  # noqa: E712
    assert result.congestion_score_grid[0, 0] > 0.0
    assert result.congested_cell_fraction == pytest.approx(1.0)


def test_high_density_and_high_flow_is_not_congested_flowing_queue(monkeypatch):
    # Same high density as above, but flow is well ABOVE the threshold —
    # a managed, flowing queue, not a stuck crowd. Proves density-alone
    # would have wrongly flagged this cell (frozen decision).
    monkeypatch.setattr(settings, "DENSITY_CONGESTION_THRESHOLD", 1.0)
    monkeypatch.setattr(settings, "FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC", 10.0)

    density_grid = np.array([[5.0]])
    velocity = np.zeros((1, 1, 2))
    velocity[0, 0] = (100.0, 0.0)  # speed=100, well above the 10 px/s threshold

    result = compute_congestion_field(_density_field(density_grid), _flow_field(velocity))

    assert result.is_congested_grid[0, 0] == False  # noqa: E712
    assert result.congestion_score_grid[0, 0] == pytest.approx(0.0)
    assert result.congested_cell_fraction == pytest.approx(0.0)


def test_low_density_never_congested_regardless_of_flow(monkeypatch):
    monkeypatch.setattr(settings, "DENSITY_CONGESTION_THRESHOLD", 1.0)
    monkeypatch.setattr(settings, "FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC", 10.0)

    density_grid = np.array([[0.05]])  # well below the 1.0 threshold

    # Low flow (would otherwise look congested if density were high).
    low_flow_velocity = np.zeros((1, 1, 2))
    low_flow_velocity[0, 0] = (1.0, 0.0)
    low_flow_result = compute_congestion_field(
        _density_field(density_grid), _flow_field(low_flow_velocity)
    )
    assert low_flow_result.is_congested_grid[0, 0] == False  # noqa: E712

    # High flow too, for completeness.
    high_flow_velocity = np.zeros((1, 1, 2))
    high_flow_velocity[0, 0] = (100.0, 0.0)
    high_flow_result = compute_congestion_field(
        _density_field(density_grid), _flow_field(high_flow_velocity)
    )
    assert high_flow_result.is_congested_grid[0, 0] == False  # noqa: E712


def test_mismatched_grid_shapes_raise_clear_error():
    density_grid = np.zeros((3, 3))
    velocity = np.zeros((4, 4, 2))

    with pytest.raises(ValueError):
        compute_congestion_field(_density_field(density_grid), _flow_field(velocity))
