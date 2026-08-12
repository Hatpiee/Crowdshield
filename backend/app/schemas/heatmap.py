import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict

from app.models.heatmap import HeatmapType


class HeatmapSnapshotRead(BaseModel):
    id: uuid.UUID
    heatmap_type: HeatmapType
    frame_number: int
    timestamp_seconds: float
    file_size_bytes: int
    created_at: datetime
    # file_path deliberately excluded — internal storage detail, not for API
    # exposure (same reasoning as VideoRead excluding storage_filename in
    # Phase 3). Image byte serving is deferred to the dashboard integration
    # phase (Resolution 3) — this schema is metadata-only.

    model_config = ConfigDict(from_attributes=True)


class HeatmapSnapshotListResponse(BaseModel):
    items: list[HeatmapSnapshotRead]
