from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.model_config import ModelConfig
from app.models.processing_run import ProcessingRun
from app.models.user import User
from app.models.video import VideoAsset
from app.schemas.session import (
    ModelConfigRead,
    ProcessingRunRead,
    SessionCreate,
    SessionListResponse,
    SessionRead,
    SessionStatusRead,
)
from app.services import session_service
from app.services.session_service import InvalidStateTransitionError, VideoNotFoundError

router = APIRouter(prefix="/sessions", tags=["sessions"])


def _not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


def _invalid_state_transition(exc: InvalidStateTransitionError) -> HTTPException:
    return HTTPException(
        status_code=409,
        detail=error_envelope("INVALID_STATE_TRANSITION", str(exc)),
    )


def _get_latest_processing_run(db: Session, session_id: UUID) -> ProcessingRun | None:
    return (
        db.query(ProcessingRun)
        .filter(ProcessingRun.session_id == session_id)
        .order_by(ProcessingRun.created_at.desc())
        .first()
    )


def _fetch_session_row(db: Session, session_id: UUID):
    return (
        db.query(AnalysisSession, VideoAsset.original_filename, User.email)
        .join(VideoAsset, AnalysisSession.video_id == VideoAsset.id)
        .join(User, AnalysisSession.created_by == User.id)
        .filter(AnalysisSession.id == session_id)
        .first()
    )


def _to_session_read(
    db: Session, session: AnalysisSession, video_filename: str, creator_email: str
) -> SessionRead:
    model_config = db.get(ModelConfig, session.model_config_id)
    latest_run = _get_latest_processing_run(db, session.id)
    return SessionRead(
        id=session.id,
        video_id=session.video_id,
        video_original_filename=video_filename,
        model_config=ModelConfigRead.model_validate(model_config),
        status=session.status,
        created_by=session.created_by,
        created_by_email=creator_email,
        created_at=session.created_at,
        latest_processing_run=(
            ProcessingRunRead.model_validate(latest_run) if latest_run else None
        ),
    )


@router.post("", status_code=201)
def create_session(
    body: SessionCreate,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    try:
        session = session_service.create_session(db, body.video_id, current_user.id)
    except VideoNotFoundError:
        raise HTTPException(
            status_code=404,
            detail=error_envelope("NOT_FOUND", "Video not found"),
        )

    video = db.get(VideoAsset, session.video_id)
    session_read = _to_session_read(
        db, session, video.original_filename, current_user.email
    )
    return success_envelope(session_read.model_dump(mode="json", by_alias=True))


@router.get("")
def list_sessions(
    limit: int = Query(50, ge=1, le=500),
    offset: int = Query(0, ge=0),
    status: SessionStatus | None = Query(None),
    video_id: UUID | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = (
        db.query(AnalysisSession, VideoAsset.original_filename, User.email)
        .join(VideoAsset, AnalysisSession.video_id == VideoAsset.id)
        .join(User, AnalysisSession.created_by == User.id)
    )
    if status is not None:
        query = query.filter(AnalysisSession.status == status)
    if video_id is not None:
        query = query.filter(AnalysisSession.video_id == video_id)

    total = query.count()
    rows = (
        query.order_by(AnalysisSession.created_at.desc())
        .limit(limit)
        .offset(offset)
        .all()
    )
    items = [
        _to_session_read(db, session, filename, email)
        for session, filename, email in rows
    ]
    response = SessionListResponse(items=items, total=total, limit=limit, offset=offset)
    return success_envelope(response.model_dump(mode="json", by_alias=True))


@router.get("/{session_id}")
def get_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = _fetch_session_row(db, session_id)
    if row is None:
        raise _not_found()
    session, filename, email = row
    session_read = _to_session_read(db, session, filename, email)
    return success_envelope(session_read.model_dump(mode="json", by_alias=True))


@router.post("/{session_id}/start")
def start_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.get(AnalysisSession, session_id)
    if session is None:
        raise _not_found()

    try:
        session_service.start_session(db, session)
    except InvalidStateTransitionError as exc:
        raise _invalid_state_transition(exc)

    row = _fetch_session_row(db, session_id)
    session, filename, email = row
    session_read = _to_session_read(db, session, filename, email)
    return success_envelope(session_read.model_dump(mode="json", by_alias=True))


@router.post("/{session_id}/cancel")
def cancel_session(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.get(AnalysisSession, session_id)
    if session is None:
        raise _not_found()

    try:
        session_service.cancel_session(db, session)
    except InvalidStateTransitionError as exc:
        raise _invalid_state_transition(exc)

    row = _fetch_session_row(db, session_id)
    session, filename, email = row
    session_read = _to_session_read(db, session, filename, email)
    return success_envelope(session_read.model_dump(mode="json", by_alias=True))


@router.get("/{session_id}/status")
def get_session_status(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    session = db.get(AnalysisSession, session_id)
    if session is None:
        raise _not_found()

    latest_run = _get_latest_processing_run(db, session_id)
    status_read = SessionStatusRead(
        id=session.id,
        status=session.status,
        latest_processing_run=(
            ProcessingRunRead.model_validate(latest_run) if latest_run else None
        ),
    )
    return success_envelope(status_read.model_dump(mode="json"))
