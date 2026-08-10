import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field

from app.models.analysis_session import SessionStatus
from app.models.processing_run import ProcessingRunStatus


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

    model_config = ConfigDict(from_attributes=True)
