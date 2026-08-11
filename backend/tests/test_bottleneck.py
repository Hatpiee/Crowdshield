import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.bottleneck import BottleneckDetector, BottleneckField
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.flow_field import FlowGridField

FRAME_WIDTH = 400
FRAME_HEIGHT = 400
FPS = 30.0
DT = 1.0 / FPS


def _grid() -> CrowdGrid:
    return CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)


def _flow_field(
    grid_mean_velocity: np.ndarray, frame_number: int, timestamp_seconds: float
) -> FlowGridField:
    rows, cols = grid_mean_velocity.shape[:2]
    return FlowGridField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        grid_mean_velocity=grid_mean_velocity,
        grid_velocity_variance=np.zeros((rows, cols)),
        grid_divergence=np.zeros((rows, cols)),
        grid_curl=np.zeros((rows, cols)),
        source_motion_frame_number=frame_number,
    )


def _convergent_velocity_grid(grid: CrowdGrid, k: float) -> np.ndarray:
    """v = -k * (pos - center): points inward toward the frame center, a
    "sink" pattern, magnitude proportional to distance from center."""
    cx, cy = FRAME_WIDTH / 2, FRAME_HEIGHT / 2
    velocity = np.zeros((grid.rows, grid.cols, 2))
    for row in range(grid.rows):
        for col in range(grid.cols):
            x, y = grid.cell_center(row, col)
            velocity[row, col, 0] = -k * (x - cx)
            velocity[row, col, 1] = -k * (y - cy)
    return velocity


def _uniform_velocity_grid(grid: CrowdGrid, vx: float, vy: float) -> np.ndarray:
    velocity = np.zeros((grid.rows, grid.cols, 2))
    velocity[..., 0] = vx
    velocity[..., 1] = vy
    return velocity


def test_convergent_flow_produces_strong_bottleneck_signal_near_center(monkeypatch):
    monkeypatch.setattr(settings, "BOTTLENECK_WINDOW_FRAMES", 10)
    grid = _grid()
    detector = BottleneckDetector(grid)

    # k*dt = 5.0 * (1/30) ~= 0.167 < 1 -> forward-Euler converges toward the
    # center geometrically each step without oscillating/overshooting past it.
    velocity = _convergent_velocity_grid(grid, k=5.0)

    result = None
    for i in range(10):
        result = detector.update(_flow_field(velocity, frame_number=i, timestamp_seconds=i * DT))

    assert result is not None
    assert result.window_frames_used == 10

    center_row, center_col = grid.rows // 2, grid.cols // 2
    assert result.bottleneck_score_grid[center_row, center_col] < 0.5
    assert result.strongest_bottleneck_cell is not None


def test_uniform_flow_produces_no_false_positive_bottleneck_signal(monkeypatch):
    monkeypatch.setattr(settings, "BOTTLENECK_WINDOW_FRAMES", 10)
    grid = _grid()
    detector = BottleneckDetector(grid)

    # Every tracer moves by the identical displacement each step -> pairwise
    # distances (and therefore spread) are EXACTLY preserved, ratio == 1.0
    # everywhere a spread could be computed at all.
    velocity = _uniform_velocity_grid(grid, vx=50.0, vy=0.0)

    result = None
    for i in range(10):
        result = detector.update(_flow_field(velocity, frame_number=i, timestamp_seconds=i * DT))

    assert result is not None
    valid = ~np.isnan(result.bottleneck_score_grid)
    assert valid.any()
    np.testing.assert_allclose(result.bottleneck_score_grid[valid], 1.0, atol=1e-6)


def test_returns_none_before_window_has_two_frames_then_real_field(monkeypatch):
    monkeypatch.setattr(settings, "BOTTLENECK_WINDOW_FRAMES", 5)
    grid = _grid()
    detector = BottleneckDetector(grid)
    velocity = _uniform_velocity_grid(grid, vx=10.0, vy=0.0)

    first = detector.update(_flow_field(velocity, frame_number=0, timestamp_seconds=0.0))
    assert first is None

    second = detector.update(_flow_field(velocity, frame_number=1, timestamp_seconds=DT))
    assert second is not None
    assert isinstance(second, BottleneckField)
    assert second.window_frames_used == 2


def test_two_detectors_do_not_share_state(monkeypatch):
    monkeypatch.setattr(settings, "BOTTLENECK_WINDOW_FRAMES", 5)
    grid = _grid()
    detector_1 = BottleneckDetector(grid)
    detector_2 = BottleneckDetector(grid)
    velocity = _uniform_velocity_grid(grid, vx=10.0, vy=0.0)

    detector_1.update(_flow_field(velocity, frame_number=0, timestamp_seconds=0.0))
    detector_1.update(_flow_field(velocity, frame_number=1, timestamp_seconds=DT))
    detector_1.update(_flow_field(velocity, frame_number=2, timestamp_seconds=2 * DT))

    # detector_2 has seen nothing yet -> still needs 2 frames before a real
    # result, regardless of detector_1's accumulated window.
    result_2_first = detector_2.update(_flow_field(velocity, frame_number=0, timestamp_seconds=0.0))
    assert result_2_first is None
