import uuid
from datetime import datetime

from pydantic import BaseModel

from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import EvidenceType, ObservationCategory


class RegionRead(BaseModel):
    x_min: float
    y_min: float
    x_max: float
    y_max: float


class EvidenceItemRead(BaseModel):
    id: uuid.UUID
    observation_id: uuid.UUID
    category: ObservationCategory
    confidence: float
    evidence_type: EvidenceType
    description: str
    region: RegionRead


class EvidencePackageRead(BaseModel):
    id: uuid.UUID
    schema_version: str
    session_id: uuid.UUID
    frame_number: int
    timestamp_seconds: float
    trigger_type: TriggerType
    trigger_reason: str
    crowd_metrics_summary: dict
    risk_state_snapshot: dict
    confidence: float
    binding_constraint: str
    complete: bool
    missing: list[str]
    contradictions: list[dict]
    evidence_items: list[EvidenceItemRead]
    created_at: datetime

    # Deliberately excludes representative_frame_path/roi_crop_path — raw
    # filesystem paths are an internal detail (same exclusion already
    # applied to every prior phase's *Read schema, e.g. VideoAsset's
    # storage_filename/file_path). Image byte serving remains deferred to
    # dashboard integration, same as video/heatmap serving.


class SessionEvidenceRead(BaseModel):
    items: list[EvidencePackageRead]
