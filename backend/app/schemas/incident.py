import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.models.incident import ClosureReason, IncidentLifecycleStatus, IncidentPriority, OperatorActionType
from app.pipeline.decision_result import RecommendationType


class OperatorActionRead(BaseModel):
    id: uuid.UUID
    action_type: OperatorActionType
    performed_by: uuid.UUID
    notes: Optional[str]
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class IncidentEvidenceRead(BaseModel):
    evidence_package_id: uuid.UUID
    decision_result_id: uuid.UUID
    correlated_at: datetime

    model_config = ConfigDict(from_attributes=True)


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
    linked_evidence: list[IncidentEvidenceRead]
    operator_actions: list[OperatorActionRead]
    created_at: datetime
    updated_at: datetime


class SessionIncidentsRead(BaseModel):
    items: list[IncidentRead]


class ActionRequest(BaseModel):
    notes: Optional[str] = None
