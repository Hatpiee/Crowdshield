"""Session Report data contract — Final Intelligence phase (Phase A/C).
Deterministic, read-only aggregation of ALREADY-PERSISTED data
(CrowdMetricsSnapshot, RiskEvent, EvidencePackage+EvidenceItem,
DecisionResultRow, Incident) into one coherent, operator-readable "analysis
report" contract. Plain dataclasses (mirrors evidence_package.py's role) —
built by session_report_service.py, this module holds only the in-memory
data contract, no I/O.

============================================================
NO NEW INFERENCE, NO NEW SOURCE OF TRUTH (Frozen Decision)
============================================================
Every field here is either copied directly from an existing persisted row
or derived via a PURE, deterministic function of already-persisted rows (a
template sentence, a severity tag, a coarse spatial-region label).
`session_report_service.py` makes ZERO Ollama/VLM/LLM calls — "Deterministic
Before Generative" (constitutional principle #1) applies to the REPORT
layer exactly as it already applies to `abstention.py`. Even the
`overview_summary`/`behavioral_analysis`/`spatial_analysis` free-text
fields are built by string-templating already-computed numbers and
REUSING already-generated per-event text (`DecisionResultRow.reasoning_
summary` / `structured_report`) — never a new LLM call synthesizing a
"whole-session narrative" from scratch. The one genuinely NEW inference
surface this phase introduces is the Operator Copilot
(`session_copilot.py`), a deliberately separate, explicitly-requested,
session-scoped Q&A feature — not a report-generation feature. See
DECISIONS.md, "Final Intelligence Phase" for the full rationale.

============================================================
EVENTS vs INCIDENTS (Frozen Decision, carried over verbatim from the
governing request)
============================================================
An `EvidencePackage` exists because a deterministic trigger (RISK/FALLBACK/
OPERATOR/ACUTE_HAZARD) fired and Loop B ran — that is an "investigated
event," independent of whatever outcome the Reasoner/abstention system
reached. A CONFIRMED INCIDENT is a real, persisted `Incident` row, created
ONLY via `incident_service.correlate_or_create_incident` for a genuinely
incident-worthy decision (a Verifier-superseded decision never creates
one). `EventSummary.status` and `SessionReportResult.confirmed_incident_
count` are ALWAYS kept structurally distinct; a session with many
investigated events and zero confirmed incidents is a valid, richly-
informative report, never presented as "no data."

`EventSummary.status` is one of exactly four values:
  OBSERVED  — a trigger fired and evidence was gathered, but either no
              decision was reached (LLM unavailable) or the Reasoner
              concluded NO_INCIDENT.
  WATCH     — the Reasoner concluded WATCH.
  ABSTAINED — the deterministic abstention system short-circuited before
              any LLM call.
  INCIDENT  — the Reasoner concluded INCIDENT (regardless of whether a
              real `Incident` row exists yet — `incident_id` is None when
              a later Verifier-driven supersession prevented one).
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional


@dataclass
class RiskOverview:
    current_state: Optional[str]  # RiskState.value; None if zero snapshots exist yet
    current_score: Optional[float]
    trend: str  # "RISING" | "FALLING" | "STABLE" | "UNKNOWN"
    trend_delta: Optional[float]  # current_score - score at the start of the trend window; None if UNKNOWN
    trend_window_seconds: Optional[float]
    # Qualitative, derived from CrowdMetricsSnapshot's own boolean-ish
    # fields (congested_cell_fraction > 0, reverse_flow_cell_fraction > 0,
    # bottleneck_signal_present) at the LATEST snapshot — never a fabricated
    # percentage breakdown (RiskScoreResult's own weighted contribution
    # values are not persisted per-snapshot, so this is deliberately
    # qualitative, not quantitative).
    primary_contributors: list[str] = field(default_factory=list)
    latest_snapshot_timestamp: Optional[float] = None
    snapshot_count: int = 0


@dataclass
class EventSummary:
    evidence_package_id: uuid.UUID
    frame_number: int
    timestamp_seconds: float
    trigger_type: str
    trigger_reason: str
    status: str  # OBSERVED | WATCH | ABSTAINED | INCIDENT
    decision_outcome: Optional[str]  # raw DecisionOutcome.value, None if no decision was ever reached
    event_classification: Optional[str]
    onset_seconds: Optional[float]
    peak_seconds: float
    duration_seconds: Optional[float]  # None ("UNKNOWN") when no event_window exists (non-acute-hazard triggers)
    severity_tag: str  # LOW | MODERATE | HIGH | CRITICAL — UI heuristic, not a stored score
    confidence: float
    location_description: str
    description: str
    observation_categories: list[str] = field(default_factory=list)
    abstention_reason: Optional[str] = None
    recommendation: Optional[str] = None
    incident_id: Optional[uuid.UUID] = None


@dataclass
class TimelineEntry:
    timestamp_seconds: float
    kind: str  # "RISK_TRANSITION" | "EVENT"
    description: str
    evidence_package_id: Optional[uuid.UUID] = None


@dataclass
class SessionReportResult:
    session_id: uuid.UUID
    session_status: str
    video_filename: str
    video_duration_seconds: Optional[float]
    risk_overview: RiskOverview
    investigated_event_count: int
    confirmed_incident_count: int
    events: list[EventSummary] = field(default_factory=list)
    timeline: list[TimelineEntry] = field(default_factory=list)
    overview_summary: str = ""
    incidents_summary: str = ""
    behavioral_analysis: str = ""
    spatial_analysis: str = ""
    top_recommendation: Optional[str] = None
