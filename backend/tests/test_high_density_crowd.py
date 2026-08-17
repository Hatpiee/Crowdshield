"""Master spec §34 gap-closing test (Testing Audit phase): Case #2 "High-
density crowd." Every existing density.py/congestion.py/risk_score.py test
either (a) hand-constructs DensityField/CongestionField objects directly
(test_congestion.py, test_risk_score.py) or (b) exercises real density
computation only for TIGHT-CLUSTER-SHAPE or UNIFORM-SPREAD assertions, never
for a genuinely packed 30-50+ point crowd routed all the way through the
real density -> congestion -> risk_score chain (test_density.py). This file
closes that gap using the same synthetic-TrackingResult technique already
established since Phase 9's own unit tests — no new video footage sourced,
per this phase's own explicit guidance.

Design note on WHY the comparison axis is flow (stalled vs flowing), not
crowd size (packed vs "sparse"): an empirical check during this phase found
DENSITY_CONGESTION_THRESHOLD's current default (0.1 people/cell) is low
enough, relative to CROWD_GRID_CELL_SIZE_PX=40's coarse grid, that even a
modestly-sized spread-out crowd's KDE tail can locally exceed it — both
DENSITY_CONGESTION_THRESHOLD and CROWD_GRID_CELL_SIZE_PX are explicitly
documented in congestion.py's own module docstring as "UNCALIBRATED ...
uncalibrated against any real venue" (see also DECISIONS.md's "Known
Structural Limitation: Pixel-Space vs. Real-World Units"), so recalibrating
them is out of scope for this testing-audit phase. Comparing the SAME packed
crowd under stalled vs. flowing motion instead reuses congestion.py's own
frozen, well-calibrated design axis ("deliberately NOT density alone" — see
its module docstring) and produces a robust, non-threshold-sensitive test.
"""

import numpy as np
import pytest

from app.pipeline.bottleneck import BottleneckField
from app.pipeline.congestion import compute_congestion_field
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import compute_density_field
from app.pipeline.detection import Point
from app.pipeline.flow_field import FlowGridField
from app.pipeline.reverse_flow import ReverseFlowField
from app.pipeline.risk_score import compute_risk_score
from app.pipeline.track import Track, TrackingResult

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
PACKED_TRACK_COUNT = 40


def _grid() -> CrowdGrid:
    return CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)


def _make_track(track_id: int, x: float, y: float) -> Track:
    return Track(track_id=track_id, point=Point(x=x, y=y), local_scale=10.0, confidence=0.9, is_lost=False)


def _tracking_result(tracks: list[Track]) -> TrackingResult:
    return TrackingResult(
        frame_number=10, timestamp_seconds=10 / 30.0, tracks=tracks,
        tracker_name="bytetrack", source_detection_count=len(tracks),
    )


def _packed_high_density_crowd() -> TrackingResult:
    # 40 people packed shoulder-to-shoulder into a SMALL real-world-plausible
    # area (a ~60x60px cluster out of the 320x240 frame) — a genuine "high-
    # density crowd" signal (concentration), distinct from test_density.py's
    # own existing 200-point UNIFORMLY SPREAD test (spatial uniformity, not
    # concentration).
    rng = np.random.default_rng(42)
    cluster_x, cluster_y = 160.0, 120.0
    xs = cluster_x + rng.uniform(-30, 30, size=PACKED_TRACK_COUNT)
    ys = cluster_y + rng.uniform(-30, 30, size=PACKED_TRACK_COUNT)
    return _tracking_result([_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))])


def _same_count_spread_across_whole_frame() -> TrackingResult:
    # Same headcount as the packed crowd, but spread across the ENTIRE
    # frame — isolates CONCENTRATION (not headcount) as the variable.
    rng = np.random.default_rng(7)
    xs = rng.uniform(10, FRAME_WIDTH - 10, size=PACKED_TRACK_COUNT)
    ys = rng.uniform(10, FRAME_HEIGHT - 10, size=PACKED_TRACK_COUNT)
    return _tracking_result([_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))])


def _uniform_flow(grid: CrowdGrid, speed_px_per_sec: float) -> FlowGridField:
    velocity = np.zeros((grid.rows, grid.cols, 2))
    velocity[..., 0] = speed_px_per_sec
    return FlowGridField(
        frame_number=10, timestamp_seconds=10 / 30,
        grid_mean_velocity=velocity,
        grid_velocity_variance=np.zeros((grid.rows, grid.cols)),
        grid_divergence=np.zeros((grid.rows, grid.cols)),
        grid_curl=np.zeros((grid.rows, grid.cols)),
        source_motion_frame_number=10,
    )


def _neutral_pressure(grid: CrowdGrid) -> CrowdPressureField:
    return CrowdPressureField(
        frame_number=10, timestamp_seconds=10 / 30,
        grid=np.zeros((grid.rows, grid.cols)), max_pressure=0.0, mean_pressure=0.0,
    )


def _neutral_bottleneck(grid: CrowdGrid) -> BottleneckField:
    return BottleneckField(
        frame_number=10, timestamp_seconds=10 / 30, window_frames_used=30,
        bottleneck_score_grid=np.full((grid.rows, grid.cols), 1.0),  # no convergence signal anywhere
        strongest_bottleneck_cell=(0, 0),
    )


def _neutral_reverse_flow(grid: CrowdGrid) -> ReverseFlowField:
    return ReverseFlowField(
        frame_number=10, timestamp_seconds=10 / 30,
        is_reverse_flow_grid=np.zeros((grid.rows, grid.cols), dtype=bool),
        reverse_flow_cell_fraction=0.0, cells_with_established_baseline=grid.rows * grid.cols,
    )


def test_packed_crowd_produces_far_higher_peak_density_than_same_headcount_spread_out():
    grid = _grid()
    packed = compute_density_field(_packed_high_density_crowd(), grid)
    spread = compute_density_field(_same_count_spread_across_whole_frame(), grid)

    assert packed.track_count == spread.track_count == PACKED_TRACK_COUNT
    # Same number of people, but concentrating them into a small area must
    # produce a dramatically higher PEAK per-cell density — this is the
    # actual real-world signal of a "high-density crowd" (people packed
    # together), not merely "many people somewhere on screen."
    assert packed.grid.max() > spread.grid.max() * 5
    # A genuinely packed real-world crowd naturally exercises density.py's
    # OWN existing "high_voronoi_disagreement" graceful-degradation path
    # (already unit-tested in isolation by test_density.py's
    # test_high_voronoi_disagreement_lowers_confidence) — tight spacing
    # makes Voronoi-cell boundaries genuinely ambiguous, lowering
    # confidence rather than crashing or fabricating certainty.
    assert packed.degradation_reason == "high_voronoi_disagreement"
    assert packed.estimation_confidence < 1.0


def test_packed_high_density_crowd_with_stalled_flow_is_congested_but_flowing_is_not():
    grid = _grid()
    packed_density = compute_density_field(_packed_high_density_crowd(), grid)

    stalled = compute_congestion_field(packed_density, _uniform_flow(grid, speed_px_per_sec=1.0))
    flowing = compute_congestion_field(packed_density, _uniform_flow(grid, speed_px_per_sec=100.0))

    # SAME real, packed high-density crowd — congestion.py's frozen design
    # (density AND stalled flow, never density alone) must still correctly
    # distinguish a genuinely stuck dense crowd from the same dense crowd
    # moving through freely (a managed, flowing queue).
    assert stalled.congested_cell_fraction > 0.0
    assert flowing.congested_cell_fraction == pytest.approx(0.0)
    assert stalled.congestion_score_grid.max() > flowing.congestion_score_grid.max()


def test_packed_high_density_stalled_crowd_yields_higher_risk_score_than_same_crowd_flowing():
    grid = _grid()
    packed_density = compute_density_field(_packed_high_density_crowd(), grid)

    stalled_congestion = compute_congestion_field(packed_density, _uniform_flow(grid, speed_px_per_sec=1.0))
    flowing_congestion = compute_congestion_field(packed_density, _uniform_flow(grid, speed_px_per_sec=100.0))

    stalled_score = compute_risk_score(
        density=packed_density, pressure=_neutral_pressure(grid),
        congestion=stalled_congestion, bottleneck=_neutral_bottleneck(grid),
        reverse_flow=_neutral_reverse_flow(grid),
    )
    flowing_score = compute_risk_score(
        density=packed_density, pressure=_neutral_pressure(grid),
        congestion=flowing_congestion, bottleneck=_neutral_bottleneck(grid),
        reverse_flow=_neutral_reverse_flow(grid),
    )

    # End-to-end confirmation, through the REAL chain (not hand-set fields
    # like test_risk_score.py's own helpers): the same high-density crowd
    # reads as meaningfully riskier when stuck than when flowing.
    assert stalled_score.risk_score > flowing_score.risk_score
    assert stalled_score.risk_score > 0.0
    assert flowing_score.risk_score == pytest.approx(0.0)
