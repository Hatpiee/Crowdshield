import uuid
from datetime import datetime

from sqlalchemy import DateTime, String, UniqueConstraint, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class ModelConfig(Base):
    __tablename__ = "model_configs"
    __table_args__ = (
        UniqueConstraint(
            "detector_model",
            "detector_runtime",
            "vlm_model",
            "llm_model",
            name="uq_model_configs_snapshot",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    detector_model: Mapped[str] = mapped_column(String, nullable=False)
    detector_runtime: Mapped[str] = mapped_column(String, nullable=False)
    vlm_model: Mapped[str] = mapped_column(String, nullable=False)
    llm_model: Mapped[str] = mapped_column(String, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
