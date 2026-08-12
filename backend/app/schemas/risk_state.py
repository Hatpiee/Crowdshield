import uuid
from datetime import datetime
from typing import Optional

from pydantic import BaseModel, ConfigDict

from app.pipeline.risk_state import RiskState


class RiskEventRead(BaseModel):
    id: uuid.UUID
    previous_state: RiskState
    new_state: RiskState
    frame_number: int
    timestamp_seconds: float
    risk_score_at_transition: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class SessionRiskRead(BaseModel):
    current_state: Optional[RiskState]
    transition_history: list[RiskEventRead]
