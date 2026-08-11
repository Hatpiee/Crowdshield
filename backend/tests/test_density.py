import numpy as np
import pytest

from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.density import (
    HIGH_DISAGREEMENT_THRESHOLD,
    TOO_FEW_POINTS_CONFIDENCE,
    compute_density_field,
)
from app.pipeline.detection import Point
from app.pipeline.track import Track, TrackingResult

FRAME_WIDTH = 320
FRAME_HEIGHT = 240


def _grid() -> CrowdGrid:
    return CrowdGrid.from_frame_dimensions(FRAME_WIDTH, FRAME_HEIGHT)


def _make_track(track_id: int, x: float, y: float, is_lost: bool = False) -> Track:
    return Track(
        track_id=track_id,
        point=Point(x=x, y=y),
        local_scale=10.0,
        confidence=0.9,
        is_lost=is_lost,
    )


def _tracking_result(tracks: list[Track], frame_number: int = 0) -> TrackingResult:
    return TrackingResult(
        frame_number=frame_number,
        timestamp_seconds=frame_number / 30.0,
        tracks=tracks,
        tracker_name="bytetrack",
        source_detection_count=len(tracks),
    )


def test_tight_cluster_produces_clear_peak_near_zero_elsewhere():
    grid = _grid()
    # Small spread (a few px) rather than exactly coincident, so KDE/Voronoi
    # both succeed normally — this test is about the density SHAPE, not the
    # degradation path (covered separately below).
    rng = np.random.default_rng(1)
    cluster_x, cluster_y = 160.0, 120.0
    xs = cluster_x + rng.uniform(-3, 3, size=12)
    ys = cluster_y + rng.uniform(-3, 3, size=12)
    tracks = [_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))]

    result = compute_density_field(_tracking_result(tracks), grid)

    peak_row, peak_col = np.unravel_index(result.grid.argmax(), result.grid.shape)
    expected_row, expected_col = grid.cell_index_for_point(cluster_x, cluster_y)
    assert (peak_row, peak_col) == (expected_row, expected_col)

    # Far corner cell should be near-zero.
    assert result.grid[0, 0] == pytest.approx(0.0, abs=0.01)


def test_uniformly_spread_points_produce_roughly_uniform_density():
    grid = _grid()
    rng = np.random.default_rng(2)
    xs = rng.uniform(10, FRAME_WIDTH - 10, size=200)
    ys = rng.uniform(10, FRAME_HEIGHT - 10, size=200)
    tracks = [_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))]

    result = compute_density_field(_tracking_result(tracks), grid)

    # Coefficient of variation well under 1 indicates roughly uniform
    # spread, not a concentrated peak (a tight cluster would have CV >> 1).
    coefficient_of_variation = result.grid.std() / result.grid.mean()
    assert coefficient_of_variation < 0.6


def test_zero_points_gives_all_zero_grid_at_full_confidence():
    grid = _grid()
    result = compute_density_field(_tracking_result([]), grid)

    assert np.all(result.grid == 0.0)
    assert result.track_count == 0
    assert result.estimation_confidence == 1.0
    assert result.degradation_reason is None


def test_one_point_gracefully_degrades_no_crash():
    grid = _grid()
    tracks = [_make_track(0, 50.0, 50.0)]

    result = compute_density_field(_tracking_result(tracks), grid)

    assert result.track_count == 1
    assert result.degradation_reason == "too_few_points"
    assert result.estimation_confidence == TOO_FEW_POINTS_CONFIDENCE
    assert result.grid.sum() == pytest.approx(1.0)


def test_two_points_gracefully_degrades_no_crash():
    grid = _grid()
    tracks = [_make_track(0, 50.0, 50.0), _make_track(1, 200.0, 150.0)]

    result = compute_density_field(_tracking_result(tracks), grid)

    assert result.track_count == 2
    assert result.degradation_reason == "too_few_points"
    assert result.estimation_confidence == TOO_FEW_POINTS_CONFIDENCE


def test_kde_normalization_recovers_input_track_count():
    grid = _grid()
    rng = np.random.default_rng(3)
    xs = rng.uniform(30, FRAME_WIDTH - 30, size=25)
    ys = rng.uniform(30, FRAME_HEIGHT - 30, size=25)
    tracks = [_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))]

    result = compute_density_field(_tracking_result(tracks), grid)

    # Renormalized by construction (see density.py's module docstring) —
    # exact to floating-point precision, not merely approximate.
    assert result.grid.sum() == pytest.approx(25.0, rel=1e-6)


def test_voronoi_disagreement_computed_and_small_for_well_conditioned_points():
    grid = _grid()
    rng = np.random.default_rng(4)
    xs = rng.uniform(30, FRAME_WIDTH - 30, size=30)
    ys = rng.uniform(30, FRAME_HEIGHT - 30, size=30)
    tracks = [_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))]

    result = compute_density_field(_tracking_result(tracks), grid)

    assert result.voronoi_disagreement_summary is not None
    assert result.voronoi_disagreement_summary < HIGH_DISAGREEMENT_THRESHOLD
    assert result.degradation_reason is None
    assert result.estimation_confidence == 1.0


def test_high_voronoi_disagreement_lowers_confidence(monkeypatch):
    import app.pipeline.density as density_module

    # Force the threshold below the well-conditioned case's real measured
    # disagreement (a small positive number) to deterministically exercise
    # the confidence-lowering branch without needing a contrived point set.
    monkeypatch.setattr(density_module, "HIGH_DISAGREEMENT_THRESHOLD", 0.0)

    grid = _grid()
    rng = np.random.default_rng(4)
    xs = rng.uniform(30, FRAME_WIDTH - 30, size=30)
    ys = rng.uniform(30, FRAME_HEIGHT - 30, size=30)
    tracks = [_make_track(i, x, y) for i, (x, y) in enumerate(zip(xs, ys))]

    result = compute_density_field(_tracking_result(tracks), grid)

    assert result.degradation_reason == "high_voronoi_disagreement"
    assert result.estimation_confidence < 1.0


def test_exactly_coincident_points_degrade_gracefully_not_crash():
    grid = _grid()
    tracks = [_make_track(i, 100.0, 100.0) for i in range(5)]

    result = compute_density_field(_tracking_result(tracks), grid)

    assert result.track_count == 5
    assert result.degradation_reason == "kde_singular_covariance"
    assert result.estimation_confidence == TOO_FEW_POINTS_CONFIDENCE
    assert result.grid.sum() == pytest.approx(5.0)


def test_near_coincident_points_kde_succeeds_but_voronoi_unavailable():
    # A genuinely tight real-world cluster (e.g. people standing shoulder
    # to shoulder) can be tight enough for Voronoi's qhull precision to
    # treat it as degenerate even though it's not exactly coincident and
    # KDE succeeds fine — a distinct graceful-degradation path from the
    # exactly-coincident case above.
    grid = _grid()
    tracks = [_make_track(i, 100.0 + i * 0.1, 100.0 + i * 0.1) for i in range(10)]

    result = compute_density_field(_tracking_result(tracks), grid)

    assert result.track_count == 10
    assert result.grid.sum() == pytest.approx(10.0)
    assert result.degradation_reason == "voronoi_unavailable"
    assert result.voronoi_disagreement_summary is None
    assert result.estimation_confidence < 1.0


def test_is_lost_tracks_excluded_from_density_input():
    grid = _grid()
    matched = [_make_track(0, 50.0, 50.0), _make_track(1, 60.0, 55.0)]
    lost = [_make_track(2, 250.0, 200.0, is_lost=True)]

    result_with_lost = compute_density_field(_tracking_result(matched + lost), grid)
    result_matched_only = compute_density_field(_tracking_result(matched), grid)

    # Only the 2 matched tracks count, regardless of the lost track present.
    assert result_with_lost.track_count == 2
    assert result_with_lost.grid.sum() == pytest.approx(result_matched_only.grid.sum())
    np.testing.assert_allclose(result_with_lost.grid, result_matched_only.grid)

    # The lost track's far-away position must not show up in the density.
    lost_row, lost_col = grid.cell_index_for_point(250.0, 200.0)
    assert result_with_lost.grid[lost_row, lost_col] == pytest.approx(0.0, abs=0.01)
