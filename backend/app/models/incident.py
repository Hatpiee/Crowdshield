"""Incident / IncidentEvidence / OperatorAction — roadmap Phase 18 (this
project's Phase 19), §19/§20/§21. Closes the loop between outcome=INCIDENT
decisions and a persistent, operator-actionable record.

RESOLUTION 1 (DISMISS has no distinct top-level state): §19's actual
lifecycle (DETECTED -> ACTIVE -> RESOLVED, or DETECTED/ACTIVE ->
FALSE_POSITIVE) never names a "DISMISSED" state, even though §20 names
DISMISS as a distinct operator action from RESOLVE. Both transition
`lifecycle_status` to RESOLVED — `closure_reason` (RESOLVED or DISMISSED)
is a separate, nullable field populated only at that transition, preserving
the real operational distinction without inventing an ungrounded lifecycle
state.

RESOLUTION 2 (ACKNOWLEDGED is orthogonal, not a lifecycle state): §19
describes ACKNOWLEDGED as "operator-set status... additionally," not part
of the diagrammed transition graph. Modeled as `acknowledged`/
`acknowledged_at`/`acknowledged_by`, independent of `lifecycle_status` — an
incident can be ACTIVE-and-acknowledged or ACTIVE-and-unacknowledged.

RESOLUTION 3 (ESCALATE is ADMIN-only, state-only): `priority` (NORMAL/
ELEVATED) is set to ELEVATED by the ESCALATE operator action — ADMIN-only
per §25's OPERATOR permission list omitting escalate (see
`api/incidents.py`'s route-level `require_role(Role.ADMIN)`). No
notification delivery exists anywhere in this project's scope.
"""

import enum
import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Text, func
from sqlalchemy import Enum as SAEnum
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.core.database import Base


class IncidentLifecycleStatus(str, enum.Enum):
    # Exactly Diagram 9's transition graph — DETECTED -> ACTIVE ->
    # RESOLVED (terminal), or DETECTED/ACTIVE -> FALSE_POSITIVE (terminal).
    DETECTED = "DETECTED"
    ACTIVE = "ACTIVE"
    RESOLVED = "RESOLVED"
    FALSE_POSITIVE = "FALSE_POSITIVE"


class ClosureReason(str, enum.Enum):
    RESOLVED = "RESOLVED"
    DISMISSED = "DISMISSED"


class IncidentPriority(str, enum.Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"


class OperatorActionType(str, enum.Enum):
    ACKNOWLEDGE = "ACKNOWLEDGE"
    DISMISS = "DISMISS"
    RESOLVE = "RESOLVE"
    MARK_FALSE_POSITIVE = "MARK_FALSE_POSITIVE"
    ESCALATE = "ESCALATE"


class Incident(Base):
    __tablename__ = "incidents"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    session_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("analysis_sessions.id"), nullable=False
    )
    lifecycle_status: Mapped[IncidentLifecycleStatus] = mapped_column(
        SAEnum(IncidentLifecycleStatus, name="incident_lifecycle_status", native_enum=True),
        nullable=False, default=IncidentLifecycleStatus.DETECTED,
    )
    closure_reason: Mapped[ClosureReason | None] = mapped_column(
        SAEnum(ClosureReason, name="closure_reason", native_enum=True), nullable=True
    )
    priority: Mapped[IncidentPriority] = mapped_column(
        SAEnum(IncidentPriority, name="incident_priority", native_enum=True),
        nullable=False, default=IncidentPriority.NORMAL,
    )
    acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    acknowledged_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    acknowledged_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )


class IncidentEvidence(Base):
    __tablename__ = "incident_evidence"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False
    )
    evidence_package_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("evidence_packages.id"), nullable=False
    )
    decision_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("decision_results.id"), nullable=False
    )
    correlated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class OperatorAction(Base):
    """Incident-scoped audit trail (§20: "all operator actions are
    persisted... never applied silently"). Deliberately NOT a general-
    purpose audit_events system spanning the whole application — that is a
    genuinely separate, later concern."""

    __tablename__ = "operator_actions"

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), primary_key=True, default=uuid.uuid4
    )
    incident_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("incidents.id"), nullable=False
    )
    action_type: Mapped[OperatorActionType] = mapped_column(
        SAEnum(OperatorActionType, name="operator_action_type", native_enum=True), nullable=False
    )
    performed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True), ForeignKey("users.id"), nullable=False
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
