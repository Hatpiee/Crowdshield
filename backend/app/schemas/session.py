import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.analysis_session import SessionStatus
from app.models.processing_run import ProcessingRunStatus
from app.pipeline.risk_state import RiskState


class SessionCreate(BaseModel):
    video_id: uuid.UUID


class ModelConfigRead(BaseModel):
    id: uuid.UUID
    detector_model: str
    detector_runtime: str
    vlm_model: str
    llm_model: str

    model_config = ConfigDict(from_attributes=True)


class ProcessingRunRead(BaseModel):
    id: uuid.UUID
    status: ProcessingRunStatus
    started_at: datetime | None
    completed_at: datetime | None
    error_message: str | None
    created_at: datetime
    # Phase 20 (Decision G): minimal progress data, surfaced on the
    # EXISTING GET /sessions/{id}/status route — no new route.
    frames_processed: int | None = None
    total_frames: int | None = None
    last_progress_update_at: datetime | None = None

    model_config = ConfigDict(from_attributes=True)


class SessionRead(BaseModel):
    id: uuid.UUID
    video_id: uuid.UUID
    video_original_filename: str
    # Pydantic v2 reserves the class attribute name `model_config` for its own
    # BaseModel configuration, so the field can't be named that in Python —
    # it's aliased to serialize (and can be constructed) as "model_config" to
    # match the API contract exactly.
    model_config_snapshot: ModelConfigRead = Field(alias="model_config")
    status: SessionStatus
    created_by: uuid.UUID
    created_by_email: str
    created_at: datetime
    latest_processing_run: ProcessingRunRead | None = None

    model_config = ConfigDict(from_attributes=True, populate_by_name=True)


class SessionListResponse(BaseModel):
    items: list[SessionRead]
    total: int
    limit: int
    offset: int


class SessionStatusRead(BaseModel):
    id: uuid.UUID
    status: SessionStatus
    latest_processing_run: ProcessingRunRead | None = None
    # Phase 21 (Step 3): from the most recent CrowdMetricsSnapshot, if any
    # exist yet — nullable so a session with zero snapshots so far (e.g.
    # QUEUED, or PROCESSING but not yet past its first checkpoint) reports
    # this honestly as "no data yet," never a fabricated 0/NORMAL. This is
    # the primary polling payload the dashboard's "current risk" and
    # "processing progress" widgets consume together in ONE request.
    latest_risk_score: float | None = None
    latest_risk_state: RiskState | None = None

    model_config = ConfigDict(from_attributes=True)
