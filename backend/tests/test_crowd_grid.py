from app.pipeline.crowd_grid import CrowdGrid


def test_grid_dimensions_derived_from_frame_size_and_cell_size(monkeypatch):
    from app.core.config import settings

    monkeypatch.setattr(settings, "CROWD_GRID_CELL_SIZE_PX", 40)

    grid = CrowdGrid.from_frame_dimensions(width=640, height=480)

    # 640/40 = 16, 480/40 = 12 exactly.
    assert grid.cols == 16
    assert grid.rows == 12
    assert grid.cell_width_px == 40.0
    assert grid.cell_height_px == 40.0
    assert grid.frame_width == 640
    assert grid.frame_height == 480


def test_density_and_flow_grids_have_identical_shapes():
    from app.pipeline.crowd_pressure import compute_crowd_pressure_field
    from app.pipeline.density import compute_density_field
    from app.pipeline.detection import Point
    from app.pipeline.flow_field import compute_flow_grid_field
    from app.pipeline.motion import MotionResult
    from app.pipeline.track import Track, TrackingResult
    import numpy as np

    grid = CrowdGrid.from_frame_dimensions(width=320, height=240)

    tracks = [
        Track(track_id=0, point=Point(x=100.0, y=100.0), local_scale=10.0, confidence=0.9)
    ]
    tracking_result = TrackingResult(
        frame_number=1, timestamp_seconds=1 / 30, tracks=tracks,
        tracker_name="bytetrack", source_detection_count=1,
    )
    density = compute_density_field(tracking_result, grid)

    motion_result = MotionResult(
        frame_number=1, prev_frame_number=0, timestamp_seconds=1 / 30,
        flow_field=np.zeros((240, 320, 2), dtype=np.float32),
        mean_velocity=0.0, velocity_variance=0.0,
        dominant_direction_degrees=None, directional_entropy=None,
        preset_used="fast", noise_floor_used=0.5,
    )
    flow = compute_flow_grid_field(motion_result, grid, elapsed_seconds=1 / 30)

    assert density.grid.shape == flow.grid_velocity_variance.shape == (grid.rows, grid.cols)

    # Also confirms compute_crowd_pressure_field accepts these shapes
    # without raising (same grid, as required by decision #1).
    pressure = compute_crowd_pressure_field(density, flow)
    assert pressure.grid.shape == (grid.rows, grid.cols)
