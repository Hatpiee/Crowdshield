import uuid
from typing import Optional

from pydantic import BaseModel


class RiskOverviewRead(BaseModel):
    current_state: Optional[str]
    current_score: Optional[float]
    trend: str
    trend_delta: Optional[float]
    trend_window_seconds: Optional[float]
    primary_contributors: list[str]
    latest_snapshot_timestamp: Optional[float]
    snapshot_count: int


class EventSummaryRead(BaseModel):
    evidence_package_id: uuid.UUID
    frame_number: int
    timestamp_seconds: float
    trigger_type: str
    trigger_reason: str
    status: str
    decision_outcome: Optional[str]
    event_classification: Optional[str]
    onset_seconds: Optional[float]
    peak_seconds: float
    duration_seconds: Optional[float]
    severity_tag: str
    confidence: float
    location_description: str
    description: str
    observation_categories: list[str]
    abstention_reason: Optional[str]
    recommendation: Optional[str]
    incident_id: Optional[uuid.UUID]


class TimelineEntryRead(BaseModel):
    timestamp_seconds: float
    kind: str
    description: str
    evidence_package_id: Optional[uuid.UUID]


class SessionReportRead(BaseModel):
    session_id: uuid.UUID
    session_status: str
    video_filename: str
    video_duration_seconds: Optional[float]
    risk_overview: RiskOverviewRead
    investigated_event_count: int
    confirmed_incident_count: int
    events: list[EventSummaryRead]
    timeline: list[TimelineEntryRead]
    overview_summary: str
    incidents_summary: str
    behavioral_analysis: str
    spatial_analysis: str
    top_recommendation: Optional[str]
