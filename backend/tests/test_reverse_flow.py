import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.flow_field import FlowGridField
from app.pipeline.reverse_flow import ReverseFlowDetector, ReverseFlowField

FRAME_WIDTH = 400
FRAME_HEIGHT = 400
FPS = 30.0
DT = 1.0 / FPS

TARGET_ROW, TARGET_COL = 5, 5


def _grid() -> CrowdGrid:
    return CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)


def _flow_field(
    grid: CrowdGrid, vx: float, vy: float, frame_number: int, timestamp_seconds: float
) -> FlowGridField:
    velocity = np.zeros((grid.rows, grid.cols, 2))
    velocity[TARGET_ROW, TARGET_COL] = (vx, vy)
    zeros = np.zeros((grid.rows, grid.cols))
    return FlowGridField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        grid_mean_velocity=velocity,
        grid_velocity_variance=zeros,
        grid_divergence=zeros,
        grid_curl=zeros,
        source_motion_frame_number=frame_number,
    )


@pytest.fixture(autouse=True)
def _fast_config(monkeypatch):
    # Small values so tests don't need dozens of frames to establish a
    # baseline / fill the persistence window. Production defaults (15/10/6)
    # are untouched outside this test module.
    monkeypatch.setattr(settings, "REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS", 5)
    monkeypatch.setattr(settings, "REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES", 5)
    monkeypatch.setattr(settings, "REVERSE_FLOW_PERSISTENCE_MIN_COUNT", 3)
    monkeypatch.setattr(settings, "REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES", 120.0)
    monkeypatch.setattr(settings, "REVERSE_FLOW_BASELINE_EMA_ALPHA", 0.1)


def test_sustained_reversal_flags_true_only_after_persistence_satisfied():
    grid = _grid()
    detector = ReverseFlowDetector(grid)

    frame_number = 0
    timestamp = 0.0

    # Establish baseline: 5 consistent frames in +x direction.
    for _ in range(5):
        result = detector.update(_flow_field(grid, 10.0, 0.0, frame_number, timestamp))
        assert result.is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == False  # noqa: E712
        frame_number += 1
        timestamp += DT

    # Now flip ~180 degrees and hold it.
    reversal_results = []
    for _ in range(4):
        result = detector.update(_flow_field(grid, -10.0, 0.0, frame_number, timestamp))
        reversal_results.append(result)
        frame_number += 1
        timestamp += DT

    # First reversed frame: not yet persistent -> still False.
    assert reversal_results[0].is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == False  # noqa: E712
    # Second reversed frame: persistence count = 2 < REVERSE_FLOW_PERSISTENCE_MIN_COUNT(3) -> still False.
    assert reversal_results[1].is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == False  # noqa: E712
    # Third reversed frame: persistence count = 3 >= 3 -> now True.
    assert reversal_results[2].is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == True  # noqa: E712
    assert reversal_results[2].reverse_flow_cell_fraction > 0.0


def test_single_transient_reversed_frame_does_not_trigger_false_positive():
    grid = _grid()
    detector = ReverseFlowDetector(grid)

    frame_number = 0
    timestamp = 0.0

    for _ in range(5):
        detector.update(_flow_field(grid, 10.0, 0.0, frame_number, timestamp))
        frame_number += 1
        timestamp += DT

    # One single reversed frame...
    reversed_result = detector.update(_flow_field(grid, -10.0, 0.0, frame_number, timestamp))
    assert reversed_result.is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == False  # noqa: E712
    frame_number += 1
    timestamp += DT

    # ...then back to consistent direction for several more frames. Persistence
    # never reaches REVERSE_FLOW_PERSISTENCE_MIN_COUNT, so it should never flip True.
    for _ in range(6):
        result = detector.update(_flow_field(grid, 10.0, 0.0, frame_number, timestamp))
        assert result.is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == False  # noqa: E712
        frame_number += 1
        timestamp += DT


def test_cell_below_min_baseline_observations_never_flags_reverse_flow():
    grid = _grid()
    detector = ReverseFlowDetector(grid)
    # Override just this test's threshold to something no amount of frames
    # below will reach.
    import app.core.config as config_module

    config_module.settings.REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS = 1000

    frame_number = 0
    timestamp = 0.0
    directions = [(10.0, 0.0), (-10.0, 0.0)] * 10  # alternating, never established

    for vx, vy in directions:
        result = detector.update(_flow_field(grid, vx, vy, frame_number, timestamp))
        assert result.is_reverse_flow_grid[TARGET_ROW, TARGET_COL] == False  # noqa: E712
        assert result.cells_with_established_baseline == 0
        frame_number += 1
        timestamp += DT

    config_module.settings.REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS = 5


def test_two_detectors_do_not_share_state():
    grid = _grid()
    detector_1 = ReverseFlowDetector(grid)
    detector_2 = ReverseFlowDetector(grid)

    frame_number = 0
    timestamp = 0.0
    for _ in range(5):
        detector_1.update(_flow_field(grid, 10.0, 0.0, frame_number, timestamp))
        frame_number += 1
        timestamp += DT

    # detector_1 has an established baseline now; detector_2 has seen nothing.
    result_1 = detector_1.update(_flow_field(grid, -10.0, 0.0, frame_number, timestamp))
    result_2 = detector_2.update(_flow_field(grid, -10.0, 0.0, 0, 0.0))

    assert result_1.cells_with_established_baseline > result_2.cells_with_established_baseline
    assert result_2.cells_with_established_baseline == 0
