import uuid
from datetime import datetime
from typing import Optional

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
    # Phase 23, Step 5: two real, already-persisted EvidencePackage columns
    # that were never exposed in this schema before — genuinely needed by
    # the incident drill-down page (VLM-call-failed handling, predictive
    # trend narrative), not new computation, just newly surfaced.
    vision_observations_present: bool
    predictive_projection_snapshot: Optional[dict]
    # Acute-Hazard Trigger Phase: both null for every RISK/FALLBACK/OPERATOR
    # package and every pre-this-phase row — populated only for a package
    # built from an ACUTE_HAZARD trigger. See evidence_package.py's
    # AcuteHazardSignalSnapshot/EventWindow for the JSON shape.
    acute_hazard_signal_snapshot: Optional[dict]
    event_window: Optional[dict]
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
