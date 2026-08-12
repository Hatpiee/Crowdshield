"""Phase 13, Step 5: RiskStateMachine — persistence, hysteresis, the
one-step-at-a-time de-escalation rule, and Resolution 2's INCIDENT
unreachability guarantee.
"""

import pytest

from app.core.config import Settings
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.risk_score import RiskScoreResult
from app.pipeline.risk_state import RiskState, RiskStateMachine

# Read from config.py's authored CLASS DEFAULTS, not the live `settings`
# singleton — the real developer .env still holds Phase 1's stale 0-1-scale
# placeholders for the three threshold keys (this project never edits the
# developer's real .env), which conftest.py's `_risk_thresholds_from_code_
# defaults` autouse fixture overrides at TEST-setup time. That fixture runs
# too late to affect these MODULE-level constants (evaluated once at import
# time), so this module reads the same source of truth the fixture uses,
# directly, keeping the two consistent regardless of fixture ordering.
_DEFAULTS = Settings.model_fields
PERSISTENCE_FRAMES = _DEFAULTS["RISK_STATE_PERSISTENCE_FRAMES"].default
RISE_ELEVATED = _DEFAULTS["RISK_ELEVATED_THRESHOLD"].default
RISE_CRITICAL = _DEFAULTS["RISK_CRITICAL_THRESHOLD"].default
FALL_ELEVATED = RISE_ELEVATED - _DEFAULTS["RISK_STATE_FALL_HYSTERESIS_MARGIN"].default
FALL_CRITICAL = RISE_CRITICAL - _DEFAULTS["RISK_STATE_FALL_HYSTERESIS_MARGIN"].default
INCIDENT_THRESHOLD = _DEFAULTS["RISK_INCIDENT_THRESHOLD"].default

# Sanity check on this test module's own assumptions about the real
# configured values (fails loudly if config.py's defaults ever change
# without this test file being revisited).
assert RISE_ELEVATED < RISE_CRITICAL < INCIDENT_THRESHOLD
assert FALL_ELEVATED < RISE_ELEVATED
assert FALL_CRITICAL < RISE_CRITICAL


def _cm(frame_number: int, timestamp_seconds: float, risk_score_value: float) -> CrowdMetrics:
    """Minimal CrowdMetrics stand-in — RiskStateMachine.update() only reads
    frame_number/timestamp_seconds/risk_score.risk_score, so every other
    field is irrelevant to these tests and left as None (a genuine
    CrowdMetrics is exercised end-to-end in scripts/preview_risk_trigger.py
    instead)."""
    risk_score = RiskScoreResult(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        risk_score=risk_score_value,
        confidence=1.0,
        contributing_signals=["pressure"],
        sub_scores={"pressure": risk_score_value},
    )
    return CrowdMetrics(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        core=None,
        congestion=None,
        bottleneck=None,
        reverse_flow=None,
        risk_score=risk_score,
        predictive_projection=None,
    )


def _feed(machine: RiskStateMachine, values: list[float], start_frame: int = 0):
    """Feeds `values` as consecutive 1-frame/1-second updates, returning the
    list of RiskStateResults in order."""
    results = []
    for i, value in enumerate(values):
        frame_number = start_frame + i
        results.append(machine.update(_cm(frame_number, float(frame_number), value)))
    return results


def test_single_anomalous_frame_never_triggers_escalation():
    machine = RiskStateMachine()
    r1 = machine.update(_cm(0, 0.0, RISE_ELEVATED + 20.0))  # well above rise threshold
    r2 = machine.update(_cm(1, 1.0, 0.0))  # drops straight back down

    assert r1.state == RiskState.NORMAL
    assert r1.state_changed_this_frame is False
    assert r2.state == RiskState.NORMAL
    assert r2.state_changed_this_frame is False


def test_persistence_confirmed_transition_occurs_at_exact_frame():
    machine = RiskStateMachine()
    above_rise = RISE_ELEVATED + 20.0
    results = _feed(machine, [above_rise] * PERSISTENCE_FRAMES)

    for i, result in enumerate(results[:-1]):
        assert result.state == RiskState.NORMAL, f"frame {i} escalated too early"
        assert result.state_changed_this_frame is False
        assert result.frames_in_current_candidate_transition == i + 1

    last = results[-1]
    assert last.state == RiskState.ELEVATED
    assert last.state_changed_this_frame is True
    # The candidate counter is reset once a transition confirms.
    assert last.frames_in_current_candidate_transition == 0


def test_hysteresis_prevents_flapping_back_to_normal():
    machine = RiskStateMachine()
    # Confirm ELEVATED first.
    _feed(machine, [RISE_ELEVATED + 20.0] * PERSISTENCE_FRAMES)

    # Oscillate around RISK_ELEVATED_THRESHOLD (the RISE threshold) — never
    # dropping to/below FALL_ELEVATED, and never exceeding RISE_CRITICAL —
    # for many frames, well beyond PERSISTENCE_FRAMES.
    oscillating_values = [RISE_ELEVATED - 2.0, RISE_ELEVATED + 5.0] * (PERSISTENCE_FRAMES * 2)
    results = _feed(machine, oscillating_values, start_frame=1000)

    for result in results:
        assert result.state == RiskState.ELEVATED
        assert result.state_changed_this_frame is False


def test_full_escalation_path_normal_to_elevated_to_critical():
    machine = RiskStateMachine()
    above_both = RISE_CRITICAL + 20.0  # also clears RISE_ELEVATED
    results = _feed(machine, [above_both] * (PERSISTENCE_FRAMES * 2))

    elevated_confirm = results[PERSISTENCE_FRAMES - 1]
    assert elevated_confirm.state == RiskState.ELEVATED
    assert elevated_confirm.state_changed_this_frame is True

    for result in results[PERSISTENCE_FRAMES:-1]:
        assert result.state == RiskState.ELEVATED
        assert result.state_changed_this_frame is False

    critical_confirm = results[-1]
    assert critical_confirm.state == RiskState.CRITICAL
    assert critical_confirm.state_changed_this_frame is True


def test_de_escalation_from_critical_returns_to_elevated_not_normal():
    machine = RiskStateMachine()
    # Reach CRITICAL first.
    _feed(machine, [RISE_CRITICAL + 20.0] * (PERSISTENCE_FRAMES * 2))

    # Sustained drop below CRITICAL's fall threshold.
    below_fall_critical = FALL_CRITICAL - 5.0
    results = _feed(
        machine, [below_fall_critical] * PERSISTENCE_FRAMES, start_frame=10_000
    )

    for result in results[:-1]:
        assert result.state == RiskState.CRITICAL
        assert result.state_changed_this_frame is False

    last = results[-1]
    assert last.state == RiskState.ELEVATED  # NOT NORMAL — one step at a time
    assert last.state_changed_this_frame is True


def test_incident_state_is_structurally_unreachable_this_phase():
    machine = RiskStateMachine()
    # Extreme, sustained, maximal risk_score for hundreds of frames.
    results = _feed(machine, [100.0] * 400)

    observed_states = {result.state for result in results}
    assert RiskState.INCIDENT not in observed_states
    assert results[-1].state == RiskState.CRITICAL  # the phase's real ceiling


def test_incident_threshold_crossed_flag_only_when_sustained_critical_and_above_incident():
    machine = RiskStateMachine()
    # Reach CRITICAL using a value BELOW the incident threshold, so the flag
    # cannot possibly already be set once CRITICAL is confirmed.
    just_above_critical = RISE_CRITICAL + 5.0
    assert just_above_critical < INCIDENT_THRESHOLD
    results = _feed(machine, [just_above_critical] * (PERSISTENCE_FRAMES * 2))
    assert results[-1].state == RiskState.CRITICAL
    assert results[-1].incident_threshold_crossed is False

    # Now sustain at/above RISK_INCIDENT_THRESHOLD while already CRITICAL.
    at_incident = INCIDENT_THRESHOLD + 1.0
    more_results = _feed(machine, [at_incident] * PERSISTENCE_FRAMES, start_frame=10_000)

    for result in more_results[:-1]:
        assert result.state == RiskState.CRITICAL  # still capped, per Resolution 2
        assert result.incident_threshold_crossed is False

    last = more_results[-1]
    assert last.state == RiskState.CRITICAL
    assert last.incident_threshold_crossed is True
    assert last.state_changed_this_frame is False  # a flag, never a transition


def test_two_risk_state_machine_instances_do_not_share_state():
    machine_a = RiskStateMachine()
    machine_b = RiskStateMachine()

    _feed(machine_a, [RISE_CRITICAL + 20.0] * (PERSISTENCE_FRAMES * 2))
    result_b = machine_b.update(_cm(0, 0.0, 0.0))

    assert machine_a._state == RiskState.CRITICAL
    assert machine_b._state == RiskState.NORMAL
    assert result_b.state == RiskState.NORMAL


def test_config_validation_rejects_out_of_order_thresholds():
    with pytest.raises(ValueError):
        Settings(
            RISK_ELEVATED_THRESHOLD=70.0,
            RISK_CRITICAL_THRESHOLD=50.0,
            RISK_INCIDENT_THRESHOLD=90.0,
        )

    with pytest.raises(ValueError):
        Settings(
            RISK_ELEVATED_THRESHOLD=40.0,
            RISK_CRITICAL_THRESHOLD=90.0,
            RISK_INCIDENT_THRESHOLD=65.0,
        )


def test_config_validation_rejects_out_of_bounds_thresholds():
    """Range-validation regression test for the new [0, 100] check.

    IMPORTANT HONEST CAVEAT (do not delete this note without re-reading it):
    this does NOT reproduce the actual real stale-.env bug documented in
    DECISIONS.md (0.5 / 0.75 / 0.9). Those values are mathematically WITHIN
    [0, 100] (0 <= 0.5 <= 100 is True) — a plain range check can never reject
    them, on 0.5, on 0.75, on 0.9, or on the task prompt's own suggested
    "e.g. 0.02" example (also within [0, 100]). A [0, 100] bound only rejects
    NEGATIVE values or values ABOVE 100 — it structurally cannot distinguish
    "correct 0-100 scale" from "stale-but-coincidentally-in-range 0-1 scale."
    See the follow-up task's response for the full explanation of this gap;
    it is reported there rather than silently fixed here, since closing it
    for real would require a DIFFERENT kind of check (e.g. a minimum
    plausible-threshold floor) that was not part of what was asked."""
    with pytest.raises(ValueError, match=r"must fall within \[0, 100\]"):
        Settings(
            RISK_ELEVATED_THRESHOLD=-5.0,
            RISK_CRITICAL_THRESHOLD=65.0,
            RISK_INCIDENT_THRESHOLD=85.0,
        )

    with pytest.raises(ValueError, match=r"must fall within \[0, 100\]"):
        Settings(
            RISK_ELEVATED_THRESHOLD=40.0,
            RISK_CRITICAL_THRESHOLD=65.0,
            RISK_INCIDENT_THRESHOLD=150.0,
        )
