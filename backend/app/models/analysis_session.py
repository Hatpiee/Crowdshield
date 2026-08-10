import enum
import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class SessionStatus(str, enum.Enum):
    # Full eventual status set defined now as a single Postgres native enum
    # type (per master spec §21) since widening an existing enum type later
    # requires its own migration for no benefit. Only CREATED, QUEUED, and
    # CANCELLED are reachable by any code path in this phase — PROCESSING,
    # COMPLETED, and FAILED require the actual processing pipeline (roadmap
    # phases 7+, e.g. Frame Extraction/Detection), which does not exist yet.
    # Their presence here is a one-time schema decision, not an
    # accidentally-implemented feature.
    CREATED = "CREATED"
    QUEUED = "QUEUED"
    PROCESSING = "PROCESSING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


class AnalysisSession(Base):
    __tablename__ = "analysis_sessions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    video_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("video_assets.id"), nullable=False
    )
    model_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False
    )
    status: Mapped[SessionStatus] = mapped_column(
        SAEnum(SessionStatus, name="session_status", native_enum=True),
        nullable=False,
        default=SessionStatus.CREATED,
    )
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
