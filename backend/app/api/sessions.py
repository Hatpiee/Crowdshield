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
from app.schemas.crowd_metrics_snapshot import CrowdMetricsSnapshotRead, CrowdMetricsTimeseriesRead
from app.schemas.session import (
    ModelConfigRead,
    ProcessingRunRead,
    SessionCreate,
    SessionListResponse,
    SessionRead,
    SessionStatusRead,
)
from app.pipeline.crowd_pressure import UNITS_DISCLAIMER as PRESSURE_UNITS_DISCLAIMER
from app.services import crowd_metrics_snapshot_service, session_service
from app.services.orchestration_launcher import launch_session_processing
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

    # Phase 20: the response payload is built HERE — deliberately BEFORE
    # launch_session_processing() spawns its background thread — and only
    # THEN is that thread launched. This ordering matters: `db`'s default
    # expire_on_commit=True means the row(s) start_session() just committed
    # are expired, so the query inside _fetch_session_row below performs a
    # genuine fresh SELECT. If the background thread were launched FIRST,
    # a video with missing/invalid metadata can race through
    # PROCESSING->FAILED fast enough (no real I/O in that failure path) to
    # land BEFORE this SELECT runs, making the response non-deterministically
    # show FAILED instead of the QUEUED state this call just set. Building
    # the response from state no concurrent writer can yet exist for keeps
    # this route's response deterministic, matching Decision A's
    # requirement ("the HTTP response still returns immediately with the
    # same shape as today") literally, not merely most of the time.
    row = _fetch_session_row(db, session_id)
    session, filename, email = row
    session_read = _to_session_read(db, session, filename, email)
    response_body = success_envelope(session_read.model_dump(mode="json", by_alias=True))

    launch_session_processing(session_id)

    return response_body


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
    latest_snapshot = crowd_metrics_snapshot_service.get_latest_snapshot(db, session_id)
    status_read = SessionStatusRead(
        id=session.id,
        status=session.status,
        latest_processing_run=(
            ProcessingRunRead.model_validate(latest_run) if latest_run else None
        ),
        latest_risk_score=latest_snapshot.risk_score if latest_snapshot else None,
        latest_risk_state=latest_snapshot.risk_state if latest_snapshot else None,
    )
    return success_envelope(status_read.model_dump(mode="json"))


@router.get("/{session_id}/crowd-metrics-timeseries")
def get_crowd_metrics_timeseries(
    session_id: UUID,
    limit: int = Query(
        crowd_metrics_snapshot_service.DEFAULT_TIMESERIES_LIMIT, ge=1, le=20000
    ),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Step 3: a new, justified route — genuinely new, real, queryable
    # time-series data (Gap 1) with no existing route to extend, same
    # reasoning already applied since Phase 12 (heatmaps got their own
    # route for the same kind of reason).
    session = db.get(AnalysisSession, session_id)
    if session is None:
        raise _not_found()

    snapshots = crowd_metrics_snapshot_service.get_session_crowd_metrics_timeseries(
        db, session_id, limit=limit
    )
    timeseries_read = CrowdMetricsTimeseriesRead(
        session_id=session_id,
        pressure_units_disclaimer=PRESSURE_UNITS_DISCLAIMER,
        items=[CrowdMetricsSnapshotRead.model_validate(row) for row in snapshots],
    )
    return success_envelope(timeseries_read.model_dump(mode="json"))
