"""CrowdMetricsSnapshot — Phase 21 (roadmap Phase 20, first half), closing
Gap 1: §21's ERD names a "crowd_metrics" entity ("Time-series snapshots of
CrowdMetrics") that was never built, since nothing needed to read it until
now. Populated at the SAME periodic checkpoint the AnalysisOrchestrator
already uses for progress/heatmap-cadence (PROGRESS_UPDATE_INTERVAL_FRAMES)
— never per-frame, per §21's "no per-frame database writes" rule — see
analysis_orchestrator.py's retrofit.

FLATTENED, NOT JSONB (new implementation decision #1): unlike some other
tables' JSON columns, this data exists specifically to be queried/charted
IN TIME ORDER (the dashboard's risk trend chart, §24) — real, indexed
columns support that; JSONB would work against it. Mirrors Phase 16's
evidence_items precedent for the same reason.

`max_pressure` is pixel-space (the established Phase 9 units caveat) — the
disclaimer STRING ITSELF is deliberately NOT stored per-row here (storage-
efficiency choice): it is a fixed, already-known constant string
(`CrowdPressureField.units_disclaimer`), so the API/frontend layer attaches
it once when serving a response rather than duplicating identical text
across what could be thousands of rows per session.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Index, Integer, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.pipeline.risk_state import RiskState


class CrowdMetricsSnapshot(Base):
    __tablename__ = "crowd_metrics_snapshots"
    __table_args__ = (
        # This table exists specifically to be queried in time order (the
        # risk trend chart, and "nearest snapshot to playback timestamp"
        # lookups) — index accordingly.
        Index("ix_crowd_metrics_snapshots_session_timestamp", "session_id", "timestamp_seconds"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_sessions.id"), nullable=False
    )
    frame_number: Mapped[int] = mapped_column(Integer, nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    # Reuses Phase 13's RiskState native enum directly — no duplicate
    # definition, same pattern as risk_events.py.
    risk_state: Mapped[RiskState] = mapped_column(
        SAEnum(RiskState, name="risk_state", native_enum=True), nullable=False
    )
    max_density: Mapped[float] = mapped_column(Float, nullable=False)
    max_pressure: Mapped[float] = mapped_column(Float, nullable=False)
    congested_cell_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    reverse_flow_cell_fraction: Mapped[float] = mapped_column(Float, nullable=False)
    bottleneck_signal_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    density_confidence: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
