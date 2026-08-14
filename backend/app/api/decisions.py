from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.user import User
from app.schemas.decision import DecisionResultRead, SessionDecisionsRead
from app.services import decision_service

# Same reasoned-exception pattern as Phase 12/13/16 (§26 doesn't explicitly
# name this route either — a genuinely persisted, queryable resource gets
# its own session-scoped + id-scoped GET routes). Documented in
# DECISIONS.md.
router = APIRouter(tags=["decisions"])


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


def _decision_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Decision result not found")
    )


@router.get("/sessions/{session_id}/decisions")
def list_session_decisions(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    rows = decision_service.get_session_decisions(db, session_id)
    response = SessionDecisionsRead(
        items=[DecisionResultRead.model_validate(row) for row in rows]
    )
    return success_envelope(response.model_dump(mode="json"))


@router.get("/decisions/{decision_id}")
def get_decision_result(
    decision_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = decision_service.get_decision_result(db, decision_id)
    if row is None:
        raise _decision_not_found()

    return success_envelope(DecisionResultRead.model_validate(row).model_dump(mode="json"))
