"""DecisionResultRow — Phase 17, §21's ERD: `decision_results` is a named
entity, distinct from `incidents` (§19, NOT built here). Insert-only,
immutable, same pattern as Phase 16's `evidence_packages`.

Reuses Phase 17's own `DecisionOutcome`/`RecommendationType` directly (no
duplicate enums) — same pattern as `TriggerType`/`ObservationCategory`/
`EvidenceType` in Phase 16's `models/evidence.py`. This is the FIRST table
needing either, so each gets its own new native Postgres enum type here.
"""

import uuid
from datetime import datetime

from sqlalchemy import DateTime, Float, ForeignKey, String, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base
from app.pipeline.decision_result import DecisionOutcome, EventClassification, RecommendationType


class DecisionResultRow(Base):
    __tablename__ = "decision_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    evidence_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_packages.id"), nullable=False
    )
    evidence_cited: Mapped[list] = mapped_column(JSONB, nullable=False)
    outcome: Mapped[DecisionOutcome] = mapped_column(
        SAEnum(DecisionOutcome, name="decision_outcome", native_enum=True), nullable=False
    )
    reasoning_summary: Mapped[str] = mapped_column(Text, nullable=False)
    recommendation: Mapped[RecommendationType | None] = mapped_column(
        SAEnum(RecommendationType, name="recommendation_type", native_enum=True),
        nullable=True,
    )
    recommendation_rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    projection_narrative: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Acute-Hazard Trigger Phase: NULLABLE — applies to every INCIDENT/WATCH
    # decision regardless of trigger type (developer's explicit choice: a
    # RISK-triggered crowd-crush incident is correctly tagged CROWD_CRUSH
    # too), null for NO_INCIDENT/ABSTAIN and for every pre-this-phase row.
    event_classification: Mapped[EventClassification | None] = mapped_column(
        SAEnum(EventClassification, name="event_classification", native_enum=True),
        nullable=True,
    )
    # NULLABLE JSONB — required only when outcome=INCIDENT (see
    # decision_result.py's EventReportSections/DecisionResult validator);
    # null for WATCH/NO_INCIDENT/ABSTAIN and every pre-this-phase row.
    structured_report: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    abstention_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, nullable=False)
    binding_constraint: Mapped[str] = mapped_column(String, nullable=False)
    # Phase 18, Decision B/C: self-referencing, NULLABLE. Populated ONLY at
    # INSERT time on a brand-new superseding ABSTAIN row (never by updating
    # an existing row) — points at the ORIGINAL decision this row
    # supersedes after a failed verification. Every Phase 17 row and every
    # non-superseding Phase 18 row has this as NULL, unconditionally.
    superseded_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_results.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
