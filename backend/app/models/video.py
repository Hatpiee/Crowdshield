import uuid
from datetime import datetime

from sqlalchemy import BigInteger, DateTime, ForeignKey, String, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VideoAsset(Base):
    __tablename__ = "video_assets"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    original_filename: Mapped[str] = mapped_column(String, nullable=False)
    storage_filename: Mapped[str] = mapped_column(String, nullable=False, unique=True)
    file_size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    mime_type: Mapped[str] = mapped_column(String, nullable=False)
    uploaded_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
