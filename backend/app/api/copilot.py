"""Operator Copilot routes — Final Intelligence phase (Phase H). Session-
scoped grounded Q&A: the ONLY data source is `session_report_service.
build_session_report()` for the session named in the URL path — the
question text is never used to look up any other session (see
session_copilot.py's module docstring for the full isolation rationale).

Unlike every other real-inference call in this project (which runs on
AnalysisOrchestrator's own background Loop B thread), this is the FIRST
route where an API request synchronously triggers a real Ollama call —
bounded by COPILOT_REQUEST_TIMEOUT_SECONDS, same failure-handling
discipline (never silently fabricate a response) as the rest of the
pipeline.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.user import User
from app.pipeline.session_copilot import (
    CopilotResponseValidationError,
    CopilotUnavailableError,
    SessionCopilot,
    suggested_questions,
)
from app.schemas.copilot import (
    CopilotAnswerRead,
    CopilotQuestionRequest,
    CopilotSuggestedQuestionsRead,
)
from app.services import session_report_service

router = APIRouter(prefix="/sessions", tags=["copilot"])


def _not_found() -> HTTPException:
    return HTTPException(status_code=404, detail=error_envelope("NOT_FOUND", "Session not found"))


@router.get("/{session_id}/copilot/suggested-questions")
def get_suggested_questions(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    report = session_report_service.build_session_report(db, session_id)
    if report is None:
        raise _not_found()

    read = CopilotSuggestedQuestionsRead(questions=suggested_questions(report))
    return success_envelope(read.model_dump(mode="json"))


@router.post("/{session_id}/copilot/ask")
def ask_copilot(
    session_id: UUID,
    body: CopilotQuestionRequest,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Session isolation: `report` is built ONLY from `session_id` (the URL
    # path parameter) — `body.question`'s text is never parsed for another
    # session id and never used to construct any DB query. Whatever the
    # operator asks, the Copilot has no other session's data available to
    # answer from.
    report = session_report_service.build_session_report(db, session_id)
    if report is None:
        raise _not_found()

    try:
        copilot = SessionCopilot()
        result = copilot.ask(body.question, report)
    except CopilotUnavailableError as exc:
        raise HTTPException(
            status_code=503,
            detail=error_envelope("COPILOT_UNAVAILABLE", str(exc)),
        )
    except CopilotResponseValidationError as exc:
        raise HTTPException(
            status_code=502,
            detail=error_envelope("COPILOT_RESPONSE_INVALID", str(exc)),
        )

    answer_read = CopilotAnswerRead(answer=result.answer, cited_timestamps=result.cited_timestamps)
    return success_envelope(answer_read.model_dump(mode="json"))
