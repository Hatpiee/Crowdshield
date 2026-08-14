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
"""

import uuid
from dataclasses import dataclass, field

from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary,
    VisionObservation,
)

SCHEMA_VERSION = "1.0"


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
