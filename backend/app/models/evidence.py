"""EvidencePackage / EvidenceItem — Phase 16 (roadmap Phase 15), §21's ERD:
TWO separate tables. `evidence_packages` holds the full JSONB payload (one
row per built EvidencePackageResult); `evidence_items` holds normalized,
FLATTENED per-observation rows for querying (§21's own stated purpose for
this table).

Reuses Phase 13's `TriggerType` and Phase 14's `ObservationCategory` /
`EvidenceType` directly (no duplicate enums) — same pattern as
`SessionStatus` (Phase 4), `HeatmapType` (Phase 12), and `RiskState` (Phase
13). This is the FIRST table needing to persist any of these three, so each
gets its own new native Postgres enum type here.

RESOLUTION 4 (immutability is an application-layer guarantee): "Once
persisted, an Evidence Package is immutable" (§16) is enforced by NEVER
WRITING an update code path in evidence_service.py — not by a database-level
trigger/constraint, which would be disproportionate for this phase's scope.
See test_evidence_persistence.py's source-level immutability test.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, Float, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import EvidenceType, ObservationCategory


class EvidencePackage(Base):
    __tablename__ = "evidence_packages"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    schema_version: Mapped[str] = mapped_column(Text, nullable=False)
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_sessions.id"), nullable=False
    )
    frame_number: Mapped[int] = mapped_column(nullable=False)
    timestamp_seconds: Mapped[float] = mapped_column(Float, nullable=False)
    trigger_type: Mapped[TriggerType] = mapped_column(
        SAEnum(TriggerType, name="trigger_type", native_enum=True), nullable=False
    )
    trigger_reason: Mapped[str] = mapped_column(Text, nullable=False)
    model_config_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("model_configs.id"), nullable=False
    )
    # Phase 16 Resolution 1: summary-level (CompactCrowdMetricsSummary),
    # never CrowdMetrics' own raw per-cell numpy grids — see
    # evidence_package.py's module docstring.
    crowd_metrics_summary: Mapped[dict] = mapped_column(JSONB, nullable=False)
    risk_state_snapshot: Mapped[dict] = mapped_column(JSONB, nullable=False)
    # Distinct from "no evidence_items rows" (decision #2) — True even when
    # the VLM genuinely returned zero observations (a valid, non-degraded
    # outcome); False only when the VLM call itself failed.
    vision_observations_present: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    binding_constraint: Mapped[str] = mapped_column(Text, nullable=False)
    complete: Mapped[bool] = mapped_column(Boolean, nullable=False)
    missing_evidence: Mapped[list] = mapped_column(JSONB, nullable=False)
    contradictions: Mapped[list] = mapped_column(JSONB, nullable=False)
    representative_frame_path: Mapped[str] = mapped_column(Text, nullable=False)
    roi_crop_path: Mapped[str] = mapped_column(Text, nullable=False)
    roi_bbox: Mapped[list] = mapped_column(JSONB, nullable=False)
    # Phase 17 decision #3: NULLABLE — absent on every "1.0"-era row (built
    # before this field existed) and on any package built without enough
    # PressureProjector history yet (Phase 11's own established pattern).
    # Never retroactively backfilled on existing rows.
    predictive_projection_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    # Acute-Hazard Trigger Phase (schema 1.2): both NULLABLE — absent on
    # every pre-1.2-era row and on every RISK/FALLBACK/OPERATOR package,
    # unconditionally. Only ever populated for a package built from an
    # ACUTE_HAZARD trigger. See evidence_package.py's AcuteHazardSignalSnapshot
    # / EventWindow dataclasses for the JSON shape.
    acute_hazard_signal_snapshot: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    event_window: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class EvidenceItem(Base):
    __tablename__ = "evidence_items"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    evidence_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_packages.id"), nullable=False
    )
    # The VisionObservation's own id, from the original Phase 14 VLM output —
    # kept distinct from this row's own primary key so a future consumer can
    # trace an item back to the exact VLM-generated observation it came from.
    observation_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False)
    category: Mapped[ObservationCategory] = mapped_column(
        SAEnum(ObservationCategory, name="observation_category", native_enum=True),
        nullable=False,
    )
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    evidence_type: Mapped[EvidenceType] = mapped_column(
        SAEnum(EvidenceType, name="evidence_type", native_enum=True), nullable=False
    )
    description: Mapped[str] = mapped_column(Text, nullable=False)
    # FLATTENED, not JSONB — deliberately, to genuinely support
    # normalized/indexed querying (§21's own stated purpose for this table),
    # not merely stored for round-trip display.
    region_x_min: Mapped[float] = mapped_column(Float, nullable=False)
    region_y_min: Mapped[float] = mapped_column(Float, nullable=False)
    region_x_max: Mapped[float] = mapped_column(Float, nullable=False)
    region_y_max: Mapped[float] = mapped_column(Float, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
