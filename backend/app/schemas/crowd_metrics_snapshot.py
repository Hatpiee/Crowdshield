import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.pipeline.risk_state import RiskState


class CrowdMetricsSnapshotRead(BaseModel):
    frame_number: int
    timestamp_seconds: float
    risk_score: float
    risk_state: RiskState
    max_density: float
    max_pressure: float
    congested_cell_fraction: float
    reverse_flow_cell_fraction: float
    bottleneck_signal_present: bool
    density_confidence: float
    created_at: datetime

    model_config = ConfigDict(from_attributes=True)


class CrowdMetricsTimeseriesRead(BaseModel):
    session_id: uuid.UUID
    # Storage-efficiency choice (crowd_metrics_snapshot.py's own docstring):
    # this fixed, already-known constant string is attached ONCE here by
    # the API layer, never duplicated per row in the database.
    pressure_units_disclaimer: str
    items: list[CrowdMetricsSnapshotRead]
