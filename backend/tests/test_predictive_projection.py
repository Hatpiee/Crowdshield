import dataclasses
import inspect

import pytest

from app.core.config import settings
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.predictive_projection import PredictiveProjection, PressureProjector


def _pressure(frame_number: int, timestamp_seconds: float, mean_pressure: float) -> CrowdPressureField:
    return CrowdPressureField(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        grid=None,  # not used by PressureProjector
        max_pressure=mean_pressure,  # not used by PressureProjector
        mean_pressure=mean_pressure,
    )


def test_linear_increasing_sequence_matches_hand_calculated_extrapolation(monkeypatch):
    monkeypatch.setattr(settings, "PREDICTIVE_WINDOW_SECONDS", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_HORIZON_SECONDS", 2.0)

    slope, intercept, dt = 5.0, 20.0, 0.1
    projector = PressureProjector()

    result = None
    for i in range(15):
        t = i * dt
        result = projector.update(_pressure(i, t, slope * t + intercept))

    assert result is not None
    latest_t = 14 * dt
    # Data is exactly linear, so the least-squares fit recovers the true
    # slope/intercept (up to floating-point noise) -> extrapolation at
    # latest_t + horizon should match the same line exactly.
    expected = slope * (latest_t + settings.PREDICTION_HORIZON_SECONDS) + intercept
    assert result.projected_pressure == pytest.approx(expected, abs=0.1)
    assert result.r_squared > 0.999
    assert result.horizon_seconds == pytest.approx(2.0)


def test_flat_sequence_projection_stays_near_current_value(monkeypatch):
    monkeypatch.setattr(settings, "PREDICTIVE_WINDOW_SECONDS", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_HORIZON_SECONDS", 5.0)

    projector = PressureProjector()
    constant_value = 15.0
    result = None
    for i in range(15):
        t = i * 0.1
        result = projector.update(_pressure(i, t, constant_value))

    assert result is not None
    assert result.projected_pressure == pytest.approx(constant_value, abs=0.05)


def test_noisy_nonmonotonic_sequence_gives_meaningfully_lower_r_squared(monkeypatch):
    monkeypatch.setattr(settings, "PREDICTIVE_WINDOW_SECONDS", 2.0)
    monkeypatch.setattr(settings, "PREDICTION_HORIZON_SECONDS", 2.0)

    projector = PressureProjector()
    # Sharply alternating, non-monotonic values -> a straight line is a
    # genuinely poor fit, unlike the clean linear case above.
    values = [5.0, 50.0, 5.0, 50.0, 5.0, 50.0, 5.0, 50.0]
    result = None
    for i, value in enumerate(values):
        result = projector.update(_pressure(i, i * 0.1, value))

    assert result is not None
    assert result.r_squared < 0.5


def test_returns_none_before_minimum_data_points_reached():
    projector = PressureProjector()

    first = projector.update(_pressure(0, 0.0, 10.0))
    assert first is None
    second = projector.update(_pressure(1, 0.1, 12.0))
    assert second is None
    third = projector.update(_pressure(2, 0.2, 14.0))
    assert third is not None
    assert isinstance(third, PredictiveProjection)
    assert third.data_points_used == 3


def test_decreasing_sequence_clamped_to_nonnegative_at_horizon(monkeypatch):
    monkeypatch.setattr(settings, "PREDICTIVE_WINDOW_SECONDS", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_HORIZON_SECONDS", 5.0)

    slope, intercept, dt = -5.0, 10.0, 0.1
    projector = PressureProjector()

    result = None
    for i in range(15):
        t = i * dt
        result = projector.update(_pressure(i, t, max(0.0, slope * t + intercept)))

    assert result is not None
    # Hand-calculated unclamped extrapolation is deeply negative (line
    # crosses zero well before the horizon) -> the clamp must kick in.
    latest_t = 14 * dt
    unclamped = slope * (latest_t + settings.PREDICTION_HORIZON_SECONDS) + intercept
    assert unclamped < 0.0
    assert result.projected_pressure == pytest.approx(0.0, abs=1e-6)
    assert result.projected_pressure >= 0.0


def test_two_projectors_do_not_share_state():
    projector_1 = PressureProjector()
    projector_2 = PressureProjector()

    for i in range(5):
        projector_1.update(_pressure(i, i * 0.1, 10.0 + i))

    # projector_2 has seen nothing yet -> still needs the minimum data
    # points before producing a real result, regardless of projector_1's history.
    result_2_first = projector_2.update(_pressure(0, 0.0, 999.0))
    assert result_2_first is None


def test_non_monotonic_timestamp_is_rejected_not_silently_absorbed(monkeypatch):
    # Regression test for the Phase 11->12 bridging check finding: a
    # non-monotonic (earlier-or-equal) timestamp must be REJECTED, not
    # silently accepted into the window — accepting it is exactly what
    # previously let the window balloon to ~2x its configured size under
    # replay (see DECISIONS.md's "Implementation-Discovered Constraints").
    monkeypatch.setattr(settings, "PREDICTIVE_WINDOW_SECONDS", 10.0)
    monkeypatch.setattr(settings, "PREDICTION_HORIZON_SECONDS", 5.0)

    projector = PressureProjector()
    for i in range(10):
        t = i * 0.5  # 0.0, 0.5, ..., 4.5
        result = projector.update(_pressure(i, t, 10.0 + i))
        assert projector.last_update_rejected is False

    assert result is not None
    window_len_before_rejection = len(projector._window)

    # Deliberately EARLIER than the most recent timestamp already in the
    # window (latest so far is 4.5) — must be rejected, not absorbed.
    rejected_result = projector.update(_pressure(999, 1.0, 500.0))

    assert rejected_result is None
    assert projector.last_update_rejected is True
    # The window must be genuinely untouched — not just "the bad point got
    # pruned afterward," but never inserted into `_window` at all.
    assert len(projector._window) == window_len_before_rejection
    assert not any(p == 500.0 for _, p in projector._window)

    # An EXACTLY-EQUAL timestamp (a duplicate) must also be rejected — the
    # guard is "<=", not just "<".
    duplicate_result = projector.update(_pressure(998, 4.5, 777.0))
    assert duplicate_result is None
    assert projector.last_update_rejected is True
    assert len(projector._window) == window_len_before_rejection

    # A subsequent VALID, monotonic update proves the projector recovers
    # cleanly and resumes normal operation — the rejection did not corrupt
    # any internal state.
    recovered_result = projector.update(_pressure(10, 5.0, 20.0))
    assert recovered_result is not None
    assert projector.last_update_rejected is False
    # data_points_used must reflect the real, correctly-bounded window
    # (previous valid points + this one), never inflated by a rejected entry.
    assert recovered_result.data_points_used == window_len_before_rejection + 1


def test_monotonic_operation_completely_unaffected_by_rejection_guard(monkeypatch):
    # Confirms the fix introduces no regression to ordinary, fully-monotonic
    # single-pass usage (the only contractually-supported usage pattern) —
    # last_update_rejected must stay False for every single call.
    monkeypatch.setattr(settings, "PREDICTIVE_WINDOW_SECONDS", 1.0)
    monkeypatch.setattr(settings, "PREDICTION_HORIZON_SECONDS", 2.0)

    slope, intercept, dt = 5.0, 20.0, 0.1
    projector = PressureProjector()

    result = None
    for i in range(15):
        t = i * dt
        result = projector.update(_pressure(i, t, slope * t + intercept))
        assert projector.last_update_rejected is False

    assert result is not None
    latest_t = 14 * dt
    expected = slope * (latest_t + settings.PREDICTION_HORIZON_SECONDS) + intercept
    assert result.projected_pressure == pytest.approx(expected, abs=0.1)
    assert result.r_squared > 0.999


def test_no_predicted_risk_score_exists_anywhere_in_this_module():
    # Structural check per the Critical Scope Boundary: only Crowd Pressure
    # is projected here — never a "predicted risk score" of any kind.
    field_names = {f.name for f in dataclasses.fields(PredictiveProjection)}
    assert "projected_pressure" in field_names
    forbidden_substrings = [
        "risk_score",
        "riskscore",
        "predicted_risk",
        "projected_risk",
    ]
    for name in field_names:
        lowered = name.lower()
        for forbidden in forbidden_substrings:
            assert forbidden not in lowered, f"unexpected field {name!r} on PredictiveProjection"

    source = inspect.getsource(PressureProjector)
    for forbidden in ["projected_risk", "predicted_risk_score", "risk_score_projection"]:
        assert forbidden not in source.lower()
