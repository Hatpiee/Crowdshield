"""VerificationResultRow — Phase 18, §21-adjacent (not literally named in
the ERD, same class of reasoned extension as prior phases' new tables —
see DECISIONS.md's Decision B entry for why THIS new table exists for a
different reason than Phase 12/13/16/17's precedents: to AVOID violating
Phase 17's already-established `decision_results` immutability guarantee,
not because no ERD entity exists for the concept).

One-way FK to `decision_results.id` (the decision it checked) — every row
here is a fresh INSERT referencing an already-existing decision_id, never
an update to that decision. `superseding_decision_id` (nullable, also FK
-> decision_results.id) is populated only when verification FAILED and a
brand-new superseding ABSTAIN decision was created (Decision C) — see
verification_service.py.
"""

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class VerificationResultRow(Base):
    __tablename__ = "verification_results"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    decision_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_results.id"), nullable=False
    )
    passed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    confidence_consistency_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    evidence_grounding_existence_ok: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # NULL when deterministically short-circuited (Decision A) — the LLM
    # was never called, so there is genuinely no LLM-checks payload.
    llm_checks: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    issues_found: Mapped[list] = mapped_column(JSONB, nullable=False)
    superseding_decision_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_results.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
