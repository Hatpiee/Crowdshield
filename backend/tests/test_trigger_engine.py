"""Phase 13, Step 5: TriggerEngine — RISK-on-confirmed-escalation-only,
cooldown + its override case, FALLBACK's independence, and priority
(OPERATOR > RISK > FALLBACK > NONE).

`crowd_metrics` is accepted-but-unused by `TriggerEngine.evaluate()` (the
frozen §28 interface shape — decision #11) so every RiskStateResult below
is hand-built directly rather than derived from a real pipeline run,
letting these tests exercise exact edge-case timing/state sequences that
would be impractical to coax out of the real pipeline. `crowd_metrics` is
passed as None throughout — safe, since it's never dereferenced.
"""

from app.core.config import Settings
from app.pipeline.acute_hazard_detector import AcuteHazardSignal
from app.pipeline.risk_state import RiskState, RiskStateResult
from app.pipeline.trigger_engine import TriggerEngine, TriggerType

# Class defaults, not the live `settings` singleton — see test_risk_state.py's
# module docstring/comment for why (developer .env staleness + fixture
# timing at MODULE import).
VLM_COOLDOWN = Settings.model_fields["VLM_COOLDOWN"].default
FALLBACK_INTERVAL = Settings.model_fields["FALLBACK_ANALYSIS_INTERVAL"].default


def _rsr(frame_number: int, timestamp_seconds: float, state: RiskState, state_changed: bool) -> RiskStateResult:
    return RiskStateResult(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        state=state,
        risk_score=50.0,
        frames_in_current_candidate_transition=0,
        incident_threshold_crossed=False,
        state_changed_this_frame=state_changed,
    )


def test_risk_fires_exactly_on_confirmed_escalation_not_every_frame():
    engine = TriggerEngine()

    # Baseline frame, no transition.
    d0 = engine.evaluate(None, _rsr(0, 0.0, RiskState.NORMAL, False))
    assert d0.trigger_type == TriggerType.NONE

    # Confirmed escalation.
    d1 = engine.evaluate(None, _rsr(1, 1.0, RiskState.ELEVATED, True))
    assert d1.trigger_type == TriggerType.RISK
    assert "NORMAL->ELEVATED" in d1.reason

    # Same elevated state, no NEW transition — must NOT fire again.
    d2 = engine.evaluate(None, _rsr(2, 1.1, RiskState.ELEVATED, False))
    assert d2.trigger_type == TriggerType.NONE
    d3 = engine.evaluate(None, _rsr(3, 1.2, RiskState.ELEVATED, False))
    assert d3.trigger_type == TriggerType.NONE


def test_cooldown_suppresses_repeated_same_level_escalation_then_allows_after_expiry():
    engine = TriggerEngine()

    # First escalation: fires.
    d1 = engine.evaluate(None, _rsr(1, 1.0, RiskState.ELEVATED, True))
    assert d1.trigger_type == TriggerType.RISK

    # De-escalates back to NORMAL (not itself a RISK condition).
    d2 = engine.evaluate(None, _rsr(2, 2.0, RiskState.NORMAL, True))
    assert d2.trigger_type == TriggerType.NONE

    # Re-escalates to the SAME level (ELEVATED) well within VLM_COOLDOWN of
    # the first firing — must be suppressed (decision #6).
    assert 2.0 + 3.0 - 1.0 < VLM_COOLDOWN
    d3 = engine.evaluate(None, _rsr(3, 2.0 + 3.0, RiskState.ELEVATED, True))
    assert d3.trigger_type == TriggerType.NONE

    # De-escalate again, then re-escalate AFTER cooldown has fully expired.
    t_deescalate = 1.0 + VLM_COOLDOWN + 1.0
    d4 = engine.evaluate(None, _rsr(4, t_deescalate, RiskState.NORMAL, True))
    assert d4.trigger_type == TriggerType.NONE

    t_reescalate = t_deescalate + 1.0
    assert t_reescalate - 1.0 >= VLM_COOLDOWN
    d5 = engine.evaluate(None, _rsr(5, t_reescalate, RiskState.ELEVATED, True))
    assert d5.trigger_type == TriggerType.RISK


def test_cooldown_override_fires_immediately_on_new_higher_escalation():
    engine = TriggerEngine()

    d1 = engine.evaluate(None, _rsr(1, 1.0, RiskState.ELEVATED, True))
    assert d1.trigger_type == TriggerType.RISK

    # A GENUINELY HIGHER escalation (ELEVATED->CRITICAL) well within cooldown.
    t2 = 1.0 + (VLM_COOLDOWN / 2)
    d2 = engine.evaluate(None, _rsr(2, t2, RiskState.CRITICAL, True))
    assert d2.trigger_type == TriggerType.RISK
    assert "override" in d2.reason.lower()


def test_fallback_fires_at_its_own_interval_independent_of_risk_state():
    engine = TriggerEngine()

    # State stays NORMAL throughout — RISK never fires.
    for t in range(0, FALLBACK_INTERVAL, 10):
        decision = engine.evaluate(None, _rsr(t, float(t), RiskState.NORMAL, False))
        assert decision.trigger_type == TriggerType.NONE

    d_at_interval = engine.evaluate(
        None, _rsr(FALLBACK_INTERVAL, float(FALLBACK_INTERVAL), RiskState.NORMAL, False)
    )
    assert d_at_interval.trigger_type == TriggerType.FALLBACK
    assert "interval elapsed" in d_at_interval.reason


def test_fallback_and_risk_cooldowns_are_independent_and_can_interleave():
    engine = TriggerEngine()

    d_risk = engine.evaluate(None, _rsr(0, 1.0, RiskState.ELEVATED, True))
    assert d_risk.trigger_type == TriggerType.RISK

    # No further state change; time passes to FALLBACK's own interval,
    # measured from session start (t=1.0), well after RISK's cooldown too —
    # but FALLBACK firing is not gated by RISK's cooldown either way.
    t_fallback = 1.0 + FALLBACK_INTERVAL
    d_fallback = engine.evaluate(
        None, _rsr(1, t_fallback, RiskState.ELEVATED, False)
    )
    assert d_fallback.trigger_type == TriggerType.FALLBACK

    # A further, genuinely new escalation still fires RISK afterward.
    t_risk2 = t_fallback + 1.0
    d_risk2 = engine.evaluate(None, _rsr(2, t_risk2, RiskState.CRITICAL, True))
    assert d_risk2.trigger_type == TriggerType.RISK


def test_operator_always_wins_over_risk_and_fallback():
    engine = TriggerEngine()
    # Warm-up call establishes session_start at t=0.0 — needed so the REAL
    # test call, made at t=FALLBACK_INTERVAL, can genuinely have FALLBACK's
    # elapsed-time condition true too (a session's very first-ever call
    # always has elapsed=0 by construction, since session_start is set to
    # that same call's own timestamp).
    engine.evaluate(None, _rsr(0, 0.0, RiskState.NORMAL, False))

    # Force both RISK's (confirmed upward transition) and FALLBACK's
    # (elapsed >= FALLBACK_ANALYSIS_INTERVAL) conditions true in this call.
    decision = engine.evaluate(
        None,
        _rsr(1, float(FALLBACK_INTERVAL), RiskState.ELEVATED, True),
        operator_requested=True,
    )
    assert decision.trigger_type == TriggerType.OPERATOR
    assert decision.reason == "operator requested"


def test_priority_risk_wins_over_fallback_when_both_true_and_fallback_stays_pending():
    engine = TriggerEngine()
    engine.evaluate(None, _rsr(0, 0.0, RiskState.NORMAL, False))  # sets session_start=0.0

    t = float(FALLBACK_INTERVAL)  # both RISK and FALLBACK conditions true now
    decision = engine.evaluate(None, _rsr(1, t, RiskState.ELEVATED, True))
    assert decision.trigger_type == TriggerType.RISK

    # FALLBACK's own timer must NOT have been consumed by the RISK-won
    # cycle — it should still fire on the very next eligible check.
    decision2 = engine.evaluate(None, _rsr(2, t + 0.5, RiskState.ELEVATED, False))
    assert decision2.trigger_type == TriggerType.FALLBACK


def _acute_signal(frame_number: int, timestamp_seconds: float, is_acute_hazard: bool) -> AcuteHazardSignal:
    return AcuteHazardSignal(
        frame_number=frame_number, timestamp_seconds=timestamp_seconds,
        is_acute_hazard=is_acute_hazard,
        corroborating_signals=["motion_energy", "flow_divergence"] if is_acute_hazard else [],
    )


# ---------------------------------------------------------------------------
# Acute-Hazard Trigger Phase: ACUTE_HAZARD branch + priority ordering
# (decision #9: OPERATOR > ACUTE_HAZARD > RISK > FALLBACK > NONE)
# ---------------------------------------------------------------------------


def test_acute_hazard_fires_when_signal_flags_and_no_operator_request():
    engine = TriggerEngine()
    decision = engine.evaluate(
        None, _rsr(1, 1.0, RiskState.NORMAL, False),
        acute_hazard_signal=_acute_signal(1, 1.0, is_acute_hazard=True),
    )
    assert decision.trigger_type == TriggerType.ACUTE_HAZARD
    assert "motion_energy" in decision.reason
    assert "flow_divergence" in decision.reason


def test_acute_hazard_signal_not_flagged_does_not_fire():
    engine = TriggerEngine()
    decision = engine.evaluate(
        None, _rsr(1, 1.0, RiskState.NORMAL, False),
        acute_hazard_signal=_acute_signal(1, 1.0, is_acute_hazard=False),
    )
    assert decision.trigger_type == TriggerType.NONE


def test_acute_hazard_preempts_risk_even_on_a_confirmed_escalation():
    engine = TriggerEngine()
    decision = engine.evaluate(
        None, _rsr(1, 1.0, RiskState.ELEVATED, True),
        acute_hazard_signal=_acute_signal(1, 1.0, is_acute_hazard=True),
    )
    assert decision.trigger_type == TriggerType.ACUTE_HAZARD


def test_operator_still_wins_over_acute_hazard():
    engine = TriggerEngine()
    decision = engine.evaluate(
        None, _rsr(1, 1.0, RiskState.NORMAL, False),
        operator_requested=True,
        acute_hazard_signal=_acute_signal(1, 1.0, is_acute_hazard=True),
    )
    assert decision.trigger_type == TriggerType.OPERATOR


def test_two_trigger_engine_instances_do_not_share_state():
    engine_a = TriggerEngine()
    engine_b = TriggerEngine()

    d_a = engine_a.evaluate(None, _rsr(0, 1.0, RiskState.ELEVATED, True))
    assert d_a.trigger_type == TriggerType.RISK

    # Engine B has seen nothing yet — an identical escalation must also
    # fire fresh, unaffected by engine_a's cooldown.
    d_b = engine_b.evaluate(None, _rsr(0, 1.0, RiskState.ELEVATED, True))
    assert d_b.trigger_type == TriggerType.RISK
