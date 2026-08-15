from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.model_config import ModelConfig
from app.models.processing_run import ProcessingRun, ProcessingRunStatus
from app.models.video import VideoAsset


class VideoNotFoundError(Exception):
    pass


class InvalidStateTransitionError(Exception):
    pass


def get_or_create_model_config(db: Session) -> ModelConfig:
    snapshot = dict(
        detector_model=settings.DETECTOR_MODEL,
        detector_runtime=settings.DETECTOR_RUNTIME,
        vlm_model=settings.VLM_MODEL,
        llm_model=settings.LLM_MODEL,
    )
    existing = db.query(ModelConfig).filter_by(**snapshot).first()
    if existing is not None:
        return existing

    config = ModelConfig(**snapshot)
    db.add(config)
    db.commit()
    db.refresh(config)
    return config


def create_session(db: Session, video_id: UUID, user_id: UUID) -> AnalysisSession:
    video = db.get(VideoAsset, video_id)
    if video is None:
        raise VideoNotFoundError(f"Video {video_id} not found")

    model_config = get_or_create_model_config(db)

    session = AnalysisSession(
        video_id=video_id,
        model_config_id=model_config.id,
        status=SessionStatus.CREATED,
        created_by=user_id,
    )
    db.add(session)
    db.commit()
    db.refresh(session)
    return session


def start_session(db: Session, session: AnalysisSession) -> AnalysisSession:
    """Only a pure state/record change — no execution of any kind. No pipeline
    stage exists yet to run (see Frozen Decisions); this just records intent."""
    if session.status != SessionStatus.CREATED:
        raise InvalidStateTransitionError(
            f"Cannot start a session in status {session.status.value}; "
            "must be CREATED"
        )

    session.status = SessionStatus.QUEUED
    processing_run = ProcessingRun(
        session_id=session.id, status=ProcessingRunStatus.PENDING
    )
    db.add(processing_run)
    db.commit()
    db.refresh(session)
    return session


def cancel_session(db: Session, session: AnalysisSession) -> AnalysisSession:
    # Phase 20: PROCESSING is a genuinely cancellable state now that the
    # AnalysisOrchestrator exists (it wasn't reachable at all in Phase 4,
    # when this function was first written for CREATED/QUEUED only).
    if session.status not in (
        SessionStatus.CREATED, SessionStatus.QUEUED, SessionStatus.PROCESSING,
    ):
        raise InvalidStateTransitionError(
            f"Cannot cancel a session in status {session.status.value}; "
            "it is already terminal"
        )

    was_processing = session.status == SessionStatus.PROCESSING
    session.status = SessionStatus.CANCELLED

    if not was_processing:
        # Phase 4's original, UNCHANGED behavior: no orchestrator is
        # running yet, so this call is the only writer that will ever
        # cancel the still-PENDING ProcessingRun.
        pending_run = (
            db.query(ProcessingRun)
            .filter(
                ProcessingRun.session_id == session.id,
                ProcessingRun.status == ProcessingRunStatus.PENDING,
            )
            .first()
        )
        if pending_run is not None:
            pending_run.status = ProcessingRunStatus.CANCELLED
            pending_run.completed_at = datetime.now(timezone.utc)
    # Phase 20 (Decision F): for an ACTIVELY PROCESSING session, this call
    # deliberately touches ONLY the AnalysisSession.status signal that the
    # orchestrator's own periodic checkpoint polls for
    # (analysis_orchestrator._fresh_status) — the ProcessingRun itself is
    # finalized to CANCELLED by the orchestrator, once it has actually
    # stopped Loop A and joined any in-flight Loop B thread(s) (Decision
    # D). Setting it here too would race with, and could be silently
    # overwritten by, the orchestrator's own eventual write.

    db.commit()
    db.refresh(session)
    return session
