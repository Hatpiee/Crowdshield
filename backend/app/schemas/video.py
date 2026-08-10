import uuid
from datetime import datetime

from pydantic import BaseModel


class VideoRead(BaseModel):
    id: uuid.UUID
    original_filename: str
    file_size_bytes: int
    mime_type: str
    uploaded_by: uuid.UUID
    uploaded_by_email: str
    created_at: datetime

    model_config = {"from_attributes": True}


class VideoListResponse(BaseModel):
    items: list[VideoRead]
    total: int
    limit: int
    offset: int
