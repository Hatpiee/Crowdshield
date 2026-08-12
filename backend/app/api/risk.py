from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.user import User
from app.schemas.risk_state import SessionRiskRead
from app.services import risk_state_service

# Mounted under the SAME "/sessions" prefix as sessions.py (Phase 4) and
# heatmaps.py (Phase 12) — a separate router, same justified-exception
# reasoning as Phase 12's heatmap_snapshots (Resolution 3): risk_events is
# a genuinely persisted, queryable resource. Route naming per §26 exactly:
# "GET /sessions/{id}/risk".
router = APIRouter(prefix="/sessions", tags=["risk"])


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


@router.get("/{session_id}/risk")
def get_session_risk(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    summary = risk_state_service.get_session_risk_summary(db, session_id)
    response = SessionRiskRead(
        current_state=summary.current_state,
        transition_history=summary.transition_history,
    )
    return success_envelope(response.model_dump(mode="json"))
