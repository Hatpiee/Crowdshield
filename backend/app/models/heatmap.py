import enum
import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, Float, ForeignKey, Integer, String, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class HeatmapType(str, enum.Enum):
    # Exactly the 5 mandatory types per master spec §13/§40 — Flow and
    # Congestion are ONE combined type, not two separate ones (frozen
    # decision). Do not add, split, or omit any member of this enum without
    # revisiting that frozen decision first.
    DENSITY = "DENSITY"
    PRESSURE = "PRESSURE"
    FLOW_CONGESTION = "FLOW_CONGESTION"
    RISK = "RISK"
    PREDICTIVE = "PREDICTIVE"


class HeatmapSnapshot(Base):
    __tablename__ = "heatmap_snapshots"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_sessions.id"), nullable=False
    )
    heatmap_type: Mapped[HeatmapType] = mapped_column(
        SAEnum(HeatmapType, name="heatmap_type", native_enum=True), nullable=False
    )
    frame_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    # Path RELATIVE to HEATMAP_STORAGE_PATH — never an absolute filesystem
    # path (which would leak deployment-environment layout and break if the
    # storage root ever moves) and never bulk pixel data itself (§13/§21/§30:
    # Postgres stores only references/metadata, large arrays live on disk).
    file_path: Mapped[str] = mapped_column(String, nullable=False)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
