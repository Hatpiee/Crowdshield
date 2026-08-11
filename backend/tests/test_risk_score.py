import numpy as np
import pytest
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.pipeline.bottleneck import BottleneckField
from app.pipeline.congestion import CongestionField
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import DensityField
from app.pipeline.reverse_flow import ReverseFlowField
from app.pipeline.risk_score import compute_risk_score

# Sanity check: these tests assume the DEFAULT weights (0.5/0.2/0.2/0.1).
assert settings.RISK_SCORE_WEIGHT_PRESSURE == pytest.approx(0.5)
assert settings.RISK_SCORE_WEIGHT_CONGESTION == pytest.approx(0.2)
assert settings.RISK_SCORE_WEIGHT_BOTTLENECK == pytest.approx(0.2)
assert settings.RISK_SCORE_WEIGHT_REVERSE_FLOW == pytest.approx(0.1)


def _density(estimation_confidence: float = 1.0, track_count: int = 5) -> DensityField:
    return DensityField(
        frame_number=10,
        timestamp_seconds=10 / 30,
        grid=np.zeros((3, 3)),
        track_count=track_count,
        estimation_confidence=estimation_confidence,
        degradation_reason=None,
        voronoi_disagreement_summary=None,
    )


def _pressure(max_pressure: float) -> CrowdPressureField:
    return CrowdPressureField(
        frame_number=10,
        timestamp_seconds=10 / 30,
        grid=np.zeros((3, 3)),
        max_pressure=max_pressure,
        mean_pressure=max_pressure / 4,
    )


def _congestion(congested_cell_fraction: float) -> CongestionField:
    return CongestionField(
        frame_number=10,
        timestamp_seconds=10 / 30,
        is_congested_grid=np.zeros((3, 3), dtype=bool),
        congestion_score_grid=np.zeros((3, 3)),
        congested_cell_fraction=congested_cell_fraction,
    )


def _bottleneck(min_ratio: float) -> BottleneckField:
    # Background of 1.0 (no convergence signal) everywhere except the one
    # seeded cell, so the grid's true minimum is always exactly min_ratio
    # (as long as min_ratio <= 1.0, true for every value used in these tests).
    grid = np.full((3, 3), 1.0)
    grid[1, 1] = min_ratio
    return BottleneckField(
        frame_number=10,
        timestamp_seconds=10 / 30,
        window_frames_used=30,
        bottleneck_score_grid=grid,
        strongest_bottleneck_cell=(1, 1),
    )


def _reverse_flow(reverse_flow_cell_fraction: float) -> ReverseFlowField:
    return ReverseFlowField(
        frame_number=10,
        timestamp_seconds=10 / 30,
        is_reverse_flow_grid=np.zeros((3, 3), dtype=bool),
        reverse_flow_cell_fraction=reverse_flow_cell_fraction,
        cells_with_established_baseline=9,
    )


def test_all_four_signals_available_matches_hand_calculated_weighted_average():
    # pressure_subscore=50 (max_pressure=50, reference=100)
    # congestion_subscore=40 (fraction=0.4)
    # bottleneck_subscore=40 ((1-0.6)*100)
    # reverse_flow_subscore=30 (fraction=0.3)
    # weighted sum = 0.5*50 + 0.2*40 + 0.2*40 + 0.1*30
    #              = 25 + 8 + 8 + 3 = 44.0
    result = compute_risk_score(
        density=_density(),
        pressure=_pressure(max_pressure=50.0),
        congestion=_congestion(congested_cell_fraction=0.4),
        bottleneck=_bottleneck(min_ratio=0.6),
        reverse_flow=_reverse_flow(reverse_flow_cell_fraction=0.3),
    )

    assert result.risk_score == pytest.approx(44.0)
    assert result.sub_scores["pressure"] == pytest.approx(50.0)
    assert result.sub_scores["congestion"] == pytest.approx(40.0)
    assert result.sub_scores["bottleneck"] == pytest.approx(40.0)
    assert result.sub_scores["reverse_flow"] == pytest.approx(30.0)
    assert result.contributing_signals == ["pressure", "congestion", "bottleneck", "reverse_flow"]


def test_bottleneck_unavailable_redistributes_weight_correctly():
    # Same sub-scores as above, but bottleneck=None this frame.
    # Remaining configured weights: pressure=0.5, congestion=0.2,
    # reverse_flow=0.1 -> sum=0.8. Redistributed:
    #   pressure     -> 0.5/0.8 = 0.625
    #   congestion   -> 0.2/0.8 = 0.25
    #   reverse_flow -> 0.1/0.8 = 0.125
    # risk_score = 0.625*50 + 0.25*40 + 0.125*30
    #            = 31.25 + 10.0 + 3.75 = 45.0
    result = compute_risk_score(
        density=_density(),
        pressure=_pressure(max_pressure=50.0),
        congestion=_congestion(congested_cell_fraction=0.4),
        bottleneck=None,
        reverse_flow=_reverse_flow(reverse_flow_cell_fraction=0.3),
    )

    assert result.risk_score == pytest.approx(45.0)
    assert "bottleneck" not in result.sub_scores
    assert result.contributing_signals == ["pressure", "congestion", "reverse_flow"]


def test_all_zero_inputs_gives_zero_risk_score_and_real_confidence():
    result = compute_risk_score(
        density=_density(estimation_confidence=1.0, track_count=0),
        pressure=_pressure(max_pressure=0.0),
        congestion=_congestion(congested_cell_fraction=0.0),
        bottleneck=_bottleneck(min_ratio=1.0),  # no convergence -> subscore 0
        reverse_flow=_reverse_flow(reverse_flow_cell_fraction=0.0),
    )

    assert result.risk_score == pytest.approx(0.0)
    # Zero people is a valid, full-confidence measurement (Phase 9's own
    # established rule) — not fabricated, genuinely propagated.
    assert result.confidence == pytest.approx(1.0)


def test_risk_score_always_clipped_to_0_100_under_adversarial_inputs():
    # Adversarially large fractions (should never occur from real Congestion/
    # ReverseFlow output, which are self-normalizing 0-1 by construction) —
    # proves the final clip is a genuine safety net, not just decorative.
    result = compute_risk_score(
        density=_density(),
        pressure=_pressure(max_pressure=1_000_000.0),
        congestion=_congestion(congested_cell_fraction=50.0),
        bottleneck=_bottleneck(min_ratio=-10.0),
        reverse_flow=_reverse_flow(reverse_flow_cell_fraction=50.0),
    )
    assert 0.0 <= result.risk_score <= 100.0
    assert result.risk_score == pytest.approx(100.0)


@pytest.mark.parametrize("confidence_value", [0.4, 0.5, 0.85, 1.0])
def test_confidence_exactly_propagated_from_density_never_invented(confidence_value):
    result = compute_risk_score(
        density=_density(estimation_confidence=confidence_value),
        pressure=_pressure(max_pressure=20.0),
        congestion=_congestion(congested_cell_fraction=0.1),
        bottleneck=_bottleneck(min_ratio=0.9),
        reverse_flow=_reverse_flow(reverse_flow_cell_fraction=0.05),
    )
    assert result.confidence == confidence_value


def test_weights_not_summing_to_one_raises_clear_config_error():
    with pytest.raises(ValidationError, match="must sum to 1.0"):
        Settings(
            RISK_SCORE_WEIGHT_PRESSURE=0.5,
            RISK_SCORE_WEIGHT_CONGESTION=0.5,
            RISK_SCORE_WEIGHT_BOTTLENECK=0.5,
            RISK_SCORE_WEIGHT_REVERSE_FLOW=0.5,
        )
