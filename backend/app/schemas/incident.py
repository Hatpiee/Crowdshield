import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.incident import ClosureReason, IncidentLifecycleStatus, IncidentPriority, OperatorActionType
from app.pipeline.decision_result import EventClassification, RecommendationType
from app.schemas.decision import DecisionResultRead
from app.schemas.evidence import EvidencePackageRead


class OperatorActionRead(BaseModel):
    id: uuid.UUID
    action_type: OperatorActionType
    performed_by: uuid.UUID
    # Phase 23, Step 5: the drill-down page's Operator Actions history needs
    # a human-readable identity per entry, not just a raw user id — resolved
    # via a small, batched lookup in api/incidents.py's own conversion
    # helper (this schema is no longer constructed via a plain
    # `.model_validate(row)` against the bare OperatorAction ORM row, which
    # has no email of its own).
    performed_by_email: str
    notes: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentTimelineEntryRead(BaseModel):
    """Phase 23, Resolution 1: the incident's TIMELINE — IncidentEvidence
    (Phase 19) already IS the timeline join table; this nests the REAL,
    already-existing EvidencePackageRead (Phase 16) and DecisionResultRead
    (Phase 17/18, which already nests verification + supersession) per
    entry, rather than the old minimal id-only shape. §24's drill-down flow
    needs the full evidence/decision content per timeline entry, not just
    its ids."""

    evidence_package: EvidencePackageRead
    decision_result: DecisionResultRead
    correlated_at: datetime


class IncidentRead(BaseModel):
    id: uuid.UUID
    session_id: uuid.UUID
    lifecycle_status: IncidentLifecycleStatus
    closure_reason: Optional[ClosureReason]
    priority: IncidentPriority
    acknowledged: bool
    acknowledged_at: Optional[datetime]
    acknowledged_by: Optional[uuid.UUID]
    latest_recommendation: Optional[RecommendationType]
    # Acute-Hazard Trigger Phase: same "latest linked decision" derivation
    # as latest_recommendation above — lets the incident list show WHY it
    # exists without opening the full drill-down.
    latest_event_classification: Optional[EventClassification]
    # Ordered chronologically by correlated_at (Resolution 1) — the
    # incident's real timeline, oldest first.
    linked_evidence: list[IncidentTimelineEntryRead]
    operator_actions: list[OperatorActionRead]
    created_at: datetime
    updated_at: datetime


class SessionIncidentsRead(BaseModel):
    items: list[IncidentRead]


class ActionRequest(BaseModel):
    notes: Optional[str] = None
