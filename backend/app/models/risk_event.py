"""RiskEvent — Phase 13, Resolution 3. Persists only CONFIRMED risk-state
TRANSITIONS (§21's ERD names "risk_events" as "Risk-state transitions") —
never one row per raw per-frame computation, consistent with the project's
"no per-frame database writes" discipline (§21) applied at the right
granularity: a session that stays NORMAL its entire run genuinely produces
ZERO rows here, which is correct, not a bug (see risk_state_service.py).

Reuses `RiskState` directly from risk_state.py (no duplicate enum) — same
pattern as `SessionStatus` (Phase 4) and `HeatmapType` (Phase 12).
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.pipeline.risk_state import RiskState


class RiskEvent(Base):
    __tablename__ = "risk_events"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_sessions.id"), nullable=False
    )
    previous_state: Mapped[RiskState] = mapped_column(
        SAEnum(RiskState, name="risk_state", native_enum=True), nullable=False
    )
    new_state: Mapped[RiskState] = mapped_column(
        SAEnum(RiskState, name="risk_state", native_enum=True), nullable=False
    )
    frame_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score_at_transition: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
