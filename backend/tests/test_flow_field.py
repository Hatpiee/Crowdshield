import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.flow_field import compute_flow_grid_field
from app.pipeline.motion import MotionResult

FRAME_WIDTH = 320
FRAME_HEIGHT = 240


def _motion_result(flow_field: np.ndarray, timestamp_seconds: float = 1 / 30) -> MotionResult:
    return MotionResult(
        frame_number=1,
        prev_frame_number=0,
        timestamp_seconds=timestamp_seconds,
        flow_field=flow_field,
        mean_velocity=0.0,
        velocity_variance=0.0,
        dominant_direction_degrees=None,
        directional_entropy=None,
        preset_used="fast",
        noise_floor_used=0.5,
    )


def test_uniform_flow_gives_near_zero_divergence_and_curl():
    grid = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    flow_field = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)
    flow_field[..., 0] = 3.0
    flow_field[..., 1] = -1.5

    result = compute_flow_grid_field(_motion_result(flow_field), grid, elapsed_seconds=1 / 30)

    np.testing.assert_allclose(result.grid_divergence, 0.0, atol=1e-6)
    np.testing.assert_allclose(result.grid_curl, 0.0, atol=1e-6)


def test_radial_explosion_gives_positive_divergence_near_center():
    grid = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    yy, xx = np.mgrid[0:FRAME_HEIGHT, 0:FRAME_WIDTH]
    cx, cy = FRAME_WIDTH / 2, FRAME_HEIGHT / 2
    # vx = k*(x-cx), vy = k*(y-cy): analytic divergence = 2k everywhere.
    k = 0.1
    flow_field = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)
    flow_field[..., 0] = k * (xx - cx)
    flow_field[..., 1] = k * (yy - cy)

    result = compute_flow_grid_field(_motion_result(flow_field), grid, elapsed_seconds=1.0)

    center_row, center_col = grid.rows // 2, grid.cols // 2
    assert result.grid_divergence[center_row, center_col] > 0
    assert result.grid_divergence[center_row, center_col] == pytest.approx(2 * k, abs=0.02)


def test_rotational_vortex_gives_curl_with_sign_matching_rotation_direction():
    grid = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    yy, xx = np.mgrid[0:FRAME_HEIGHT, 0:FRAME_WIDTH]
    cx, cy = FRAME_WIDTH / 2, FRAME_HEIGHT / 2
    k = 0.1

    # vx = -k*(y-cy), vy = k*(x-cx): analytic curl = 2k everywhere.
    flow_ccw = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)
    flow_ccw[..., 0] = -k * (yy - cy)
    flow_ccw[..., 1] = k * (xx - cx)
    result_ccw = compute_flow_grid_field(_motion_result(flow_ccw), grid, elapsed_seconds=1.0)

    # Reversed rotation: curl sign must flip.
    flow_cw = -flow_ccw
    result_cw = compute_flow_grid_field(_motion_result(flow_cw), grid, elapsed_seconds=1.0)

    center_row, center_col = grid.rows // 2, grid.cols // 2
    assert result_ccw.grid_curl[center_row, center_col] > 0
    assert result_ccw.grid_curl[center_row, center_col] == pytest.approx(2 * k, abs=0.02)
    assert result_cw.grid_curl[center_row, center_col] < 0
    assert result_cw.grid_curl[center_row, center_col] == pytest.approx(-2 * k, abs=0.02)


def test_variable_cell_has_higher_velocity_variance_than_uniform_cell():
    grid = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    flow_field = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)

    # Cell (0, 0): perfectly uniform flow.
    x0, y0, x1, y1 = grid.cell_bounds(0, 0)
    flow_field[int(y0):int(y1), int(x0):int(x1), 0] = 5.0

    # Cell (0, 1): highly variable flow (checkerboard of opposite speeds).
    x0b, y0b, x1b, y1b = grid.cell_bounds(0, 1)
    region = flow_field[int(y0b):int(y1b), int(x0b):int(x1b), 0]
    region[::2, ::2] = 50.0
    region[1::2, 1::2] = -50.0

    result = compute_flow_grid_field(_motion_result(flow_field), grid, elapsed_seconds=1.0)

    assert result.grid_velocity_variance[0, 1] > result.grid_velocity_variance[0, 0]
    assert result.grid_velocity_variance[0, 0] == pytest.approx(0.0, abs=1e-6)


def test_grid_mean_velocity_is_true_pixels_per_second_not_frame_interval():
    grid = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    # Known pixel shift: 12px in x, 4px in y, over a KNOWN real elapsed
    # time of 0.2s (deliberately NOT a "1 frame at 30fps" interval, to
    # prove this isn't secretly assuming frame_step timing).
    flow_field = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)
    flow_field[..., 0] = 12.0
    flow_field[..., 1] = 4.0
    elapsed_seconds = 0.2

    result = compute_flow_grid_field(
        _motion_result(flow_field), grid, elapsed_seconds=elapsed_seconds
    )

    # Hand-calculated: 12px / 0.2s = 60 px/s ; 4px / 0.2s = 20 px/s.
    # NOT the frame-interval value (which would just be 12.0 / 4.0).
    expected_vx = 12.0 / 0.2
    expected_vy = 4.0 / 0.2
    np.testing.assert_allclose(result.grid_mean_velocity[..., 0], expected_vx, rtol=1e-5)
    np.testing.assert_allclose(result.grid_mean_velocity[..., 1], expected_vy, rtol=1e-5)


def test_divergence_and_curl_are_grid_size_independent_for_identical_motion(monkeypatch):
    yy, xx = np.mgrid[0:FRAME_HEIGHT, 0:FRAME_WIDTH]
    cx, cy = FRAME_WIDTH / 2, FRAME_HEIGHT / 2
    k = 0.1
    flow_field = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)
    flow_field[..., 0] = k * (xx - cx)
    flow_field[..., 1] = k * (yy - cy)
    motion_result = _motion_result(flow_field)

    monkeypatch.setattr(settings, "CROWD_GRID_CELL_SIZE_PX", 40)
    grid_fine = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    result_fine = compute_flow_grid_field(motion_result, grid_fine, elapsed_seconds=1.0)

    monkeypatch.setattr(settings, "CROWD_GRID_CELL_SIZE_PX", 80)
    grid_coarse = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    result_coarse = compute_flow_grid_field(motion_result, grid_coarse, elapsed_seconds=1.0)

    center_fine = result_fine.grid_divergence[grid_fine.rows // 2, grid_fine.cols // 2]
    center_coarse = result_coarse.grid_divergence[grid_coarse.rows // 2, grid_coarse.cols // 2]

    # Same underlying physical motion -> same divergence, regardless of
    # grid resolution, BECAUSE numpy.gradient is given explicit real pixel
    # spacing (decision #7). If it used default unit spacing instead, these
    # would differ by roughly the ratio of cell sizes (2x here) — exactly
    # the config-dependent bug this decision exists to prevent.
    assert center_fine == pytest.approx(center_coarse, rel=0.1)
    assert center_fine == pytest.approx(2 * k, abs=0.03)


def test_elapsed_seconds_must_be_positive():
    grid = CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)
    flow_field = np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2), dtype=np.float32)

    with pytest.raises(ValueError):
        compute_flow_grid_field(_motion_result(flow_field), grid, elapsed_seconds=0.0)
