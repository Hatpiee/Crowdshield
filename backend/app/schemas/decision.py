import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.pipeline.decision_result import DecisionOutcome, EventClassification, RecommendationType


class VerificationResultRead(BaseModel):
    """Small, useful shape — NOT raw llm_checks verbatim (that would be
    redundant with issues_found): a per-dimension boolean summary (None
    for the four LLM-checked dimensions when deterministically short-
    circuited, per Decision A) plus the plain-language issues_found list."""

    id: uuid.UUID
    passed: bool
    confidence_consistency_ok: bool
    evidence_grounding_existence_ok: bool
    reasoning_consistency_ok: Optional[bool]
    contradiction_handling_ok: Optional[bool]
    recommendation_consistency_ok: Optional[bool]
    unsupported_claims_ok: Optional[bool]
    issues_found: list[str]
    superseding_decision_id: Optional[uuid.UUID]
    created_at: datetime


class DecisionResultRead(BaseModel):
    id: uuid.UUID
    evidence_package_id: uuid.UUID
    evidence_cited: list[str]
    outcome: DecisionOutcome
    reasoning_summary: str
    recommendation: Optional[RecommendationType]
    recommendation_rationale: Optional[str]
    projection_narrative: Optional[str]
    # Acute-Hazard Trigger Phase: additive, both optional. event_classification
    # is populated for every INCIDENT/WATCH decision regardless of trigger
    # type; structured_report only for outcome=INCIDENT. Both null on every
    # pre-this-phase row, WATCH-with-no-structured-report, and NO_INCIDENT/
    # ABSTAIN.
    event_classification: Optional[EventClassification] = None
    # Raw dict passthrough, same convention as evidence.py's
    # crowd_metrics_summary/risk_state_snapshot/contradictions — a nested
    # JSONB blob, not re-typed into its own Pydantic model at this layer.
    structured_report: Optional[dict] = None
    abstention_reason: Optional[str]
    confidence: float
    binding_constraint: str
    superseded_decision_id: Optional[uuid.UUID]
    verification: Optional[VerificationResultRead] = None
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionDecisionsRead(BaseModel):
    items: list[DecisionResultRead]
