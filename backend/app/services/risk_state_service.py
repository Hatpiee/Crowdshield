"""Persistence layer for confirmed risk-state transitions (Phase 13,
Resolution 3). Mirrors heatmap_service.py's I/O-around-pure-logic pattern:
`RiskStateMachine`/`TriggerEngine` themselves are pure/in-memory (Phase
9-12 discipline); this module is the ONLY place a `RiskEvent` row gets
written, and only on a genuinely confirmed transition.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional

from sqlalchemy.orm import Session

from app.models.risk_event import RiskEvent
from app.pipeline.risk_state import RiskState, RiskStateResult


def record_transition_if_confirmed(
    db: Session,
    session_id: uuid.UUID,
    previous_result: Optional[RiskStateResult],
    new_result: RiskStateResult,
) -> Optional[RiskEvent]:
    """Creates and commits a RiskEvent row ONLY when
    `new_result.state_changed_this_frame` is True — returns None otherwise.
    `previous_result` supplies the pre-transition state; None is only valid
    when `new_result` is itself the very first update() call of a session
    (which can never be a confirmed transition, since RiskStateMachine
    starts at NORMAL and needs RISK_STATE_PERSISTENCE_FRAMES consecutive
    frames before confirming anything)."""
    if not new_result.state_changed_this_frame:
        return None

    if previous_result is None:
        previous_state = RiskState.NORMAL
    else:
        previous_state = previous_result.state

    event = RiskEvent(
        session_id=session_id,
        previous_state=previous_state,
        new_state=new_result.state,
        frame_number=new_result.frame_number,
        timestamp_seconds=new_result.timestamp_seconds,
        risk_score_at_transition=new_result.risk_score,
    )
    db.add(event)
    db.commit()
    db.refresh(event)
    return event


@dataclass
class SessionRiskSummary:
    current_state: Optional[RiskState]
    transition_history: list[RiskEvent] = field(default_factory=list)


def get_session_risk_summary(db: Session, session_id: uuid.UUID) -> SessionRiskSummary:
    rows = (
        db.query(RiskEvent)
        .filter(RiskEvent.session_id == session_id)
        .order_by(RiskEvent.created_at.asc())
        .all()
    )
    # A session with zero transitions genuinely has zero rows (Resolution 3)
    # — current_state=None is the CORRECT representation of "never
    # escalated," not an error condition.
    current_state = rows[-1].new_state if rows else None
    return SessionRiskSummary(current_state=current_state, transition_history=rows)
