"""Evidence Package data contract — roadmap Phase 15 (this project's Phase
16), §16/§17/§28. The structural contract binding Crowd Intelligence Engine
(Phases 9-11), Risk State + Trigger Engine (Phase 13), and Vision
Intelligence (Phases 14-15) outputs together for a future Decision
Intelligence layer to consume. Built by EvidenceBuilder (evidence_builder.py)
— this module holds ONLY the in-memory data contract, no I/O.

Plain dataclasses, not Pydantic — this is an internal, backend-only return
type (EvidenceBuilder.build()'s output, before persistence), never parsed
from untrusted external input and never serialized directly to an API
response (schemas/evidence.py owns that boundary, same separation already
used for RiskStateResult/TriggerDecision vs. their *Read schemas).

RESOLUTION 1 (verbatim-means-summary): §16 describes CrowdMetrics being
embedded "verbatim," but CrowdMetrics carries full per-cell numpy grids —
embedding those into a persisted JSONB column would conflict with this
project's own storage discipline (§13/§21/§30: bulk spatial arrays live on
disk, never in relational storage). "Verbatim" is interpreted here as the
SAME summary-level shape Phase 14 already built for the VLM's own prompt
context: `CompactCrowdMetricsSummary`, reused directly rather than
inventing a second, slightly-different summary shape. Logged in
DECISIONS.md.

SCHEMA EVOLUTION (Phase 17 decision #3): `predictive_projection_snapshot`
is a genuinely NEW field, not present on any "1.0" package — Phase 17 (the
Reasoner) is EvidencePackage's first consumer needing Phase 11's
`PredictiveProjection` (§8 requires narrating "the deterministic pressure
forecast"). `SCHEMA_VERSION` is bumped to "1.1" for NEWLY-BUILT packages
only; existing persisted "1.0" rows are never retroactively modified — this
is the first real exercise of the versioning mechanism Phase 16 built
specifically to accommodate this kind of additive evolution.
"""

import uuid
from dataclasses import dataclass, field
from typing import Optional

from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary,
    VisionObservation,
)

SCHEMA_VERSION = "1.2"


@dataclass
class AcuteHazardSignalSnapshot:
    """Acute-Hazard Trigger Phase — compact, narration-safe snapshot of WHY
    an ACUTE_HAZARD trigger fired. Deliberately NOT the full
    `AcuteHazardSignal` (which carries a numpy localization grid, unsuitable
    for JSONB/narration) — same "compact summary, never the raw internal
    object" discipline as `CompactCrowdMetricsSummary`. None for every
    RISK/FALLBACK/OPERATOR package, unconditionally."""

    corroborating_signals: list[str]
    z_scores: dict[str, float]


@dataclass
class EventWindow:
    """Acute-Hazard Trigger Phase — baseline/onset/peak/aftermath timing for
    an ACUTE_HAZARD package, per the request's explicit "if exact onset
    cannot be determined, represent an interval/window" instruction. A
    single before/after frame pair cannot pinpoint a precise onset instant
    within the lookback window, so `onset_window_start_seconds` is
    genuinely represented as the START of an interval, not a fabricated
    exact moment — `peak_timestamp_seconds` (the triggering frame itself)
    is the one value here that IS exact."""

    onset_window_start_seconds: float
    peak_timestamp_seconds: float
    context_frame_timestamp_seconds: Optional[float] = None


@dataclass
class PredictiveProjectionSnapshot:
    """Compact narration-ready subset of Phase 11's `PredictiveProjection`
    (§8, decision #3) — deliberately NOT the full dataclass: only the three
    values the Reasoner is allowed to narrate (never recompute or invent).
    `window_seconds_used`/`data_points_used`/`frame_number`/
    `timestamp_seconds` are internal fitting diagnostics, not narration
    inputs, and are intentionally left out of this snapshot."""

    projected_pressure: float
    horizon_seconds: float
    r_squared: float


@dataclass
class Contradiction:
    contradiction_type: str
    description: str
    # Always "UNRESOLVED" (decision #3) — there is no reasoning layer yet
    # (Decision Intelligence, a later phase) able to actually resolve a
    # detected contradiction. This phase can only DETECT and RECORD.
    resolution_status: str = "UNRESOLVED"


@dataclass
class RiskStateSnapshot:
    """A small subset of Phase 13's RiskStateResult — the fields relevant to
    an evidence package's point-in-time record, per Resolution 6 (a single
    triggering frame, not a genuine multi-frame span)."""

    frame_number: int
    timestamp_seconds: float
    state: str  # RiskState.value — plain str so this survives JSONB round-trips cleanly
    risk_score: float


@dataclass
class EvidencePackageResult:
    """In-memory return type of EvidenceBuilder.build(), before persistence.
    Every field named in §16's EvidencePackage structure is present."""

    package_id: uuid.UUID
    schema_version: str
    session_id: uuid.UUID
    frame_number: int
    timestamp_seconds: float
    trigger_type: TriggerType
    trigger_reason: str
    model_config_id: uuid.UUID
    crowd_metrics_summary: CompactCrowdMetricsSummary
    risk_state_snapshot: RiskStateSnapshot
    vision_observations: list[VisionObservation]  # may be empty
    # Distinct from "vision_observations == []" per decision #2 — an empty
    # list from a call that genuinely SUCCEEDED is a valid, non-degraded
    # outcome; False here means the VLM call itself failed and evidence is
    # therefore genuinely MISSING, not merely "nothing to report."
    vlm_call_succeeded: bool
    confidence: float
    binding_constraint: str
    complete: bool
    missing: list[str] = field(default_factory=list)
    contradictions: list[Contradiction] = field(default_factory=list)
    representative_frame_path: str = ""
    roi_crop_path: str = ""
    roi_bbox: tuple[float, float, float, float] = (0.0, 0.0, 0.0, 0.0)
    # Phase 17 decision #3: None whenever PressureProjector hasn't
    # accumulated enough history yet (Phase 11's own established pattern —
    # never fabricated), or on any "1.0"-era package built before this field
    # existed.
    predictive_projection_snapshot: Optional[PredictiveProjectionSnapshot] = None
    # Acute-Hazard Trigger Phase (schema 1.2): both None for every
    # RISK/FALLBACK/OPERATOR package (unconditionally) and for every
    # pre-1.2-era persisted row — populated only when trigger_type ==
    # ACUTE_HAZARD. Same additive-schema-evolution discipline as Phase 17's
    # predictive_projection_snapshot addition (1.0 -> 1.1).
    acute_hazard_signal_snapshot: Optional[AcuteHazardSignalSnapshot] = None
    event_window: Optional[EventWindow] = None
