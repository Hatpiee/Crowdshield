import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProcessingRunStatus(str, enum.Enum):
    # Same rationale as SessionStatus: the full eventual status set is
    # defined now as a single Postgres native enum type. Phase 20 (the
    # AnalysisOrchestrator) is the first phase to make RUNNING, COMPLETED,
    # and FAILED genuinely reachable — see test_analysis_orchestrator.py.
    PENDING = "PENDING"
    RUNNING = "RUNNING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class ProcessingRun(Base):
    __tablename__ = "processing_runs"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_sessions.id"), nullable=False
    )
    status: Mapped[ProcessingRunStatus] = mapped_column(
        SAEnum(ProcessingRunStatus, name="processing_run_status", native_enum=True),
        nullable=False,
        default=ProcessingRunStatus.PENDING,
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    completed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    # Phase 20 (AnalysisOrchestrator, Decision G): minimal progress DATA
    # only — no push/streaming updates (roadmap Phase 22, out of scope).
    # All three nullable: null until a real run's Loop A reaches its first
    # periodic checkpoint (PROGRESS_UPDATE_INTERVAL_FRAMES frames in);
    # total_frames is set once total_frames is known (right after the
    # video is opened), frames_processed/last_progress_update_at are
    # updated at the same periodic checkpoint as cancellation-checking
    # (Decision F), never per-frame.
    frames_processed: Mapped[int | None] = mapped_column(Integer, nullable=True)
    total_frames: Mapped[int | None] = mapped_column(Integer, nullable=True)
    last_progress_update_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
