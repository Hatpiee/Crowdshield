"""GET /api/v1/system/status — Phase 21, closing the second half of Gap 2's
missing-route finding: §26 names this route distinct from GET /health
(Phase 1, still separately unauthenticated and unchanged). Implements
decision #6's three genuine, independent checks — never a fabricated green
checkmark for any of them.
"""

import logging

import httpx
import ollama
from fastapi import APIRouter, Depends
from sqlalchemy import func, text
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import success_envelope
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.user import User

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/system", tags=["system"])

# Deliberately SHORT — this is a live status-page check, not a real
# inference call. A system status widget that itself hangs for
# VLM_REQUEST_TIMEOUT_SECONDS (60s) on a genuinely-down Ollama would be a
# worse user experience than a fast, honest "unreachable."
OLLAMA_STATUS_CHECK_TIMEOUT_SECONDS = 5.0

# Same exception set minicpm_vlm.py/reasoner.py/verifier.py already use to
# distinguish "Ollama itself is unreachable" from "a genuine model
# response we need to validate" — reused here for the same reason, not
# duplicated logic (no dedicated shared client helper exists in this
# codebase to import instead; each adapter already independently
# constructs its own ollama.Client the same way this route does).
_OLLAMA_UNAVAILABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    httpx.TimeoutException,
    httpx.ConnectError,
    ollama.RequestError,
    ollama.ResponseError,
)


def _check_database(db: Session) -> dict:
    try:
        db.execute(text("SELECT 1"))
        return {"status": "ok", "detail": None}
    except Exception as exc:
        logger.warning("system/status: database check failed: %s", exc)
        return {"status": "degraded", "detail": str(exc)}


def _check_ollama() -> dict:
    try:
        client = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=OLLAMA_STATUS_CHECK_TIMEOUT_SECONDS
        )
        client.list()
        return {"status": "ok", "detail": None}
    except _OLLAMA_UNAVAILABLE_EXCEPTIONS as exc:
        logger.warning("system/status: Ollama check failed: %s", exc)
        return {"status": "degraded", "detail": str(exc)}


def _check_processing_sessions(db: Session) -> dict:
    # A real count, not a boolean. Defensive try/except for the same
    # reason _check_database and _check_ollama have their own: this check
    # also depends on the database, so a genuine DB outage legitimately
    # degrades it too — but that must surface as this check's OWN
    # {"status": "degraded"}, never an unhandled exception that crashes
    # the whole /system/status response (and with it, the independent
    # Ollama check's already-computed result).
    try:
        count = (
            db.query(func.count(AnalysisSession.id))
            .filter(AnalysisSession.status == SessionStatus.PROCESSING)
            .scalar()
        )
        return {"status": "ok", "count": count}
    except Exception as exc:
        logger.warning("system/status: processing-sessions check failed: %s", exc)
        return {"status": "degraded", "count": None, "detail": str(exc)}


@router.get("/status")
def get_system_status(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    database = _check_database(db)
    ollama_check = _check_ollama()
    processing_sessions = _check_processing_sessions(db)

    return success_envelope(
        {
            "database": database,
            "ollama": ollama_check,
            "processing_sessions": processing_sessions,
        }
    )
