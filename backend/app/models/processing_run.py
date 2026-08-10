import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ProcessingRunStatus(str, enum.Enum):
    # Same rationale as SessionStatus: the full eventual status set is
    # defined now as a single Postgres native enum type. Only PENDING and
    # CANCELLED are reachable in this phase — RUNNING, COMPLETED, and FAILED
    # require the actual processing pipeline (roadmap phases 7+), which does
    # not exist yet.
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
