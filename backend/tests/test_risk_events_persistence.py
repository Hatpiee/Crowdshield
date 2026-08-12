"""Phase 13, Step 5: risk_state_service — real DB, Resolution 3's
persistence discipline (ONLY confirmed transitions get a row).
"""

from app.core.config import Settings, settings
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.risk_score import RiskScoreResult
from app.pipeline.risk_state import RiskState, RiskStateMachine
from app.services import risk_state_service, session_service

# Class defaults, not the live `settings` singleton at MODULE-import time —
# see test_risk_state.py's comment for why. `settings` itself (used inside
# the test functions below, AFTER the autouse fixture has run) is fine.
PERSISTENCE_FRAMES = Settings.model_fields["RISK_STATE_PERSISTENCE_FRAMES"].default


def _cm(frame_number: int, timestamp_seconds: float, risk_score_value: float) -> CrowdMetrics:
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


def test_exactly_two_confirmed_transitions_produce_exactly_two_rows(
    db_session, make_video, test_user
):
    video = make_video()
    user, _ = test_user
    session = session_service.create_session(db_session, video.id, user.id)

    machine = RiskStateMachine()
    from app.models.risk_event import RiskEvent

    # Sequence: many NORMAL no-change frames, then a sustained escalation
    # to ELEVATED (transition #1), many no-change ELEVATED frames, then a
    # sustained escalation to CRITICAL (transition #2).
    values = (
        [10.0] * 20  # calm, no transition
        + [settings.RISK_ELEVATED_THRESHOLD + 20.0] * PERSISTENCE_FRAMES  # -> ELEVATED
        + [settings.RISK_ELEVATED_THRESHOLD + 20.0] * 20  # sustained, no NEW transition
        + [settings.RISK_CRITICAL_THRESHOLD + 20.0] * PERSISTENCE_FRAMES  # -> CRITICAL
    )

    previous_result = None
    for i, value in enumerate(values):
        result = machine.update(_cm(i, float(i), value))
        risk_state_service.record_transition_if_confirmed(
            db_session, session.id, previous_result, result
        )
        previous_result = result

    rows = (
        db_session.query(RiskEvent).filter(RiskEvent.session_id == session.id).all()
    )
    assert len(rows) == 2
    states = sorted((row.previous_state, row.new_state) for row in rows)
    assert states == sorted(
        [
            (RiskState.NORMAL, RiskState.ELEVATED),
            (RiskState.ELEVATED, RiskState.CRITICAL),
        ]
    )


def test_zero_transition_session_summary_has_no_error(db_session, make_video, test_user):
    video = make_video()
    user, _ = test_user
    session = session_service.create_session(db_session, video.id, user.id)

    summary = risk_state_service.get_session_risk_summary(db_session, session.id)
    assert summary.current_state is None
    assert summary.transition_history == []
