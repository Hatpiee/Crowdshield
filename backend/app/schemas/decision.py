import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.pipeline.decision_result import DecisionOutcome, RecommendationType


class DecisionResultRead(BaseModel):
    id: uuid.UUID
    evidence_package_id: uuid.UUID
    evidence_cited: list[str]
    outcome: DecisionOutcome
    reasoning_summary: str
    recommendation: Optional[RecommendationType]
    recommendation_rationale: Optional[str]
    projection_narrative: Optional[str]
    abstention_reason: Optional[str]
    confidence: float
    binding_constraint: str
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionDecisionsRead(BaseModel):
    items: list[DecisionResultRead]
