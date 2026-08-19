"""Decision Result data contract — roadmap Phase 16 Part 1 (this project's
Phase 17), §8/§17/§28. `Reasoner.reason(evidence_package) -> DecisionResult`
is the sole entry point (reasoner.py); this module holds only the data
contract, no LLM-calling logic.

Pydantic, not a plain dataclass (unlike Phase 16's EvidencePackageResult) —
`_LLMDecisionDraft` below becomes the actual JSON schema sent to Ollama's
`format` parameter, so Pydantic is required here, not a style choice.

============================================================
STRUCTURAL CONFIDENCE GUARANTEE (Frozen Decision, "Confidence Propagation,
Never Invention")
============================================================
`_LLMDecisionDraft` — what the model is ACTUALLY asked to produce — has NO
`confidence` field. This is not an instruction the model could ignore; the
model has no field to fill in, at the schema level. `DecisionResult.confidence`
is set ONLY by application code (reasoner.py), always copied directly from
`evidence_package.confidence` — never computed, never invented, never
influenced by the LLM's own output in any way.

============================================================
DECISION #2 — EVIDENCE-FIRST SCHEMA FIELD ORDER (enforced, not just styled)
============================================================
`_LLMDecisionDraft` declares `evidence_cited` BEFORE `outcome` in FIELD
DECLARATION ORDER. Ollama's grammar-constrained structured generation
produces JSON object keys in the schema's `properties` declaration order,
so this forces the model to generate its cited evidence before it is even
able to generate its conclusion — directly implementing "Evidence Before
Reasoning," countering the documented tendency of models to reach a
conclusion first and retrofit citations after. See
test_reasoner.py::test_schema_field_order_evidence_cited_before_outcome for
the test that inspects the actual generated JSON schema and proves this.

============================================================
LLM OUTCOME IS NEVER ABSTAIN (structural, not prompt-level)
============================================================
Per the Key Architectural Decision (abstention is a deterministic, pre-LLM
check — see abstention.py), the LLM is NEVER even called when abstention
applies (reasoner.py short-circuits first). `_LLMDecisionDraft.outcome` is
therefore typed as `Literal[DecisionOutcome.INCIDENT, DecisionOutcome.WATCH,
DecisionOutcome.NO_INCIDENT]` — NOT the full `DecisionOutcome` enum — so the
JSON schema sent to Ollama structurally cannot even offer ABSTAIN as an
option the model could pick. `abstention_reason` is populated by
application code ONLY on the deterministic short-circuit path, never asked
of the LLM.
"""

import enum
import uuid
from typing import Literal, Optional

from pydantic import BaseModel, Field, model_validator

_REASONING_SUMMARY_MAX_LENGTH = 1000
_RECOMMENDATION_RATIONALE_MAX_LENGTH = 500
_PROJECTION_NARRATIVE_MAX_LENGTH = 500
_EVIDENCE_CITATION_MAX_LENGTH = 200


class DecisionOutcome(str, enum.Enum):
    INCIDENT = "INCIDENT"
    WATCH = "WATCH"
    NO_INCIDENT = "NO_INCIDENT"
    # A legitimate, successful output (§40) — never forced into a binary
    # true/false. Reachable ONLY via the deterministic should_abstain()
    # short-circuit, never as an LLM-selected value (see module docstring).
    ABSTAIN = "ABSTAIN"


class EventClassification(str, enum.Enum):
    """Acute-Hazard Trigger Phase (decision 4, "expand semantic event
    classification") — applies to EVERY INCIDENT/WATCH decision, regardless
    of which trigger produced it (developer's explicit choice: a RISK-
    triggered crowd-crush incident is correctly tagged CROWD_CRUSH too, not
    left blank). The model MAY abstain into UNKNOWN when evidence is
    genuinely insufficient to classify — never forced to guess a specific
    category."""

    CROWD_CRUSH = "CROWD_CRUSH"
    STAMPEDE_LIKE_DISPERSAL = "STAMPEDE_LIKE_DISPERSAL"
    EXPLOSIVE_EVENT = "EXPLOSIVE_EVENT"
    FIRE_OR_SMOKE = "FIRE_OR_SMOKE"
    VEHICLE_INCIDENT = "VEHICLE_INCIDENT"
    STRUCTURAL_HAZARD = "STRUCTURAL_HAZARD"
    OBSTRUCTION_OR_BARRIER = "OBSTRUCTION_OR_BARRIER"
    OTHER_ACUTE_HAZARD = "OTHER_ACUTE_HAZARD"
    UNKNOWN = "UNKNOWN"


# Reasoner Stability phase — TIGHTENED from an original 800
# (real-measured root cause: real Qwen3-8B think=False generation on this
# CPU-served Ollama instance runs at only ~2.5-3.3 tokens/sec once past a
# handful of sentences — see scripts/measure_reasoner_latency.py's real
# n=3 measurements and DECISIONS.md. An 800-char budget PER FIELD across 5
# free-text fields (~1000 tokens alone, before evidence_cited/
# reasoning_summary/recommendation_rationale) was structurally incapable
# of finishing inside any reasonable timeout — all 3 real diagnostic
# INCIDENT-case calls at the old budget exceeded even a 135s timeout and
# never completed at all. 320 chars/field (~80 tokens) keeps each section
# genuinely informative (2-4 sentences) while keeping total generation
# bounded — the report stays SIX real, distinct sections; only per-field
# VERBOSITY was cut, not content areas.
_EVENT_REPORT_SECTION_MAX_LENGTH = 320
_OBSERVED_EVIDENCE_MAX_ITEMS = 6
_OBSERVED_EVIDENCE_ITEM_MAX_LENGTH = 160


class EventReportSections(BaseModel):
    """Acute-Hazard Trigger Phase — the structured incident report the
    request asks for, as a genuinely nested/queryable object rather than
    prose folded into `reasoning_summary` (developer's explicit choice).
    Nested Ollama-constrained schemas are an already-proven pattern in this
    codebase (`_ObservationListSchema` -> `_ObservationDraft` ->
    `NormalizedBoundingBox`). Required only when `outcome == INCIDENT` (see
    DecisionResult's own validator below) — WATCH keeps the simpler
    existing `reasoning_summary` only."""

    event_summary: str = Field(max_length=_EVENT_REPORT_SECTION_MAX_LENGTH)
    observed_evidence: list[str] = Field(
        min_length=1,
        max_length=_OBSERVED_EVIDENCE_MAX_ITEMS,
        description=(
            "Explicit, itemized observations actually supported by the "
            "evidence provided — never invented. Each item must be SHORT "
            f"(<= {_OBSERVED_EVIDENCE_ITEM_MAX_LENGTH} chars) and concrete "
            "(e.g. 'abrupt scene-wide motion change', 'sudden smoke/dust "
            "plume') — a phrase, not a sentence."
        ),
    )
    behavioral_analysis: str = Field(max_length=_EVENT_REPORT_SECTION_MAX_LENGTH)
    spatial_analysis: str = Field(max_length=_EVENT_REPORT_SECTION_MAX_LENGTH)
    temporal_analysis: str = Field(max_length=_EVENT_REPORT_SECTION_MAX_LENGTH)
    crowd_risk_context: str = Field(max_length=_EVENT_REPORT_SECTION_MAX_LENGTH)


class RecommendationType(str, enum.Enum):
    # Sourced directly from the TechNova problem statement's own explicit
    # "Intelligent Recommendations" list — not invented (decision #1).
    OPEN_ALTERNATE_EXITS = "OPEN_ALTERNATE_EXITS"
    CLOSE_ENTRY_GATES = "CLOSE_ENTRY_GATES"
    REDIRECT_INCOMING_VISITORS = "REDIRECT_INCOMING_VISITORS"
    DEPLOY_ADDITIONAL_SECURITY = "DEPLOY_ADDITIONAL_SECURITY"
    BROADCAST_PUBLIC_ANNOUNCEMENT = "BROADCAST_PUBLIC_ANNOUNCEMENT"
    CHANGE_BARRICADE_CONFIGURATION = "CHANGE_BARRICADE_CONFIGURATION"
    RECOMMEND_ONE_WAY_FLOW = "RECOMMEND_ONE_WAY_FLOW"


class _LLMDecisionDraft(BaseModel):
    """The ONLY schema actually sent to Ollama's `format` parameter. Field
    declaration order matters (decision #2) — do not reorder without
    re-reading the module docstring above."""

    evidence_cited: list[str] = Field(
        min_length=1,
        description=(
            "Short references to specific evidence, each naming either a "
            "compact_metrics field (e.g. 'risk_score', "
            "'congested_cell_fraction') or a specific vision observation_id "
            "— not vague free-text paraphrasing."
        ),
    )
    outcome: Literal[
        DecisionOutcome.INCIDENT, DecisionOutcome.WATCH, DecisionOutcome.NO_INCIDENT
    ]
    reasoning_summary: str = Field(max_length=_REASONING_SUMMARY_MAX_LENGTH)
    recommendation: Optional[RecommendationType] = None
    recommendation_rationale: Optional[str] = Field(
        default=None, max_length=_RECOMMENDATION_RATIONALE_MAX_LENGTH
    )
    projection_narrative: Optional[str] = Field(
        default=None, max_length=_PROJECTION_NARRATIVE_MAX_LENGTH
    )
    # Acute-Hazard Trigger Phase (decision 4/6): required whenever outcome
    # is INCIDENT or WATCH (same nullability shape as `recommendation`
    # above), null otherwise — see the business-rule validator below.
    event_classification: Optional[EventClassification] = None
    # Required only when outcome == INCIDENT — see the validator below.
    structured_report: Optional[EventReportSections] = None


class DecisionResult(BaseModel):
    """The full, persisted decision — `_LLMDecisionDraft`'s fields extended
    with server-set fields (decision_id, confidence, binding_constraint)
    application code adds AFTER the model responds (or entirely without
    calling the model at all, on the abstention short-circuit path)."""

    decision_id: uuid.UUID
    evidence_package_id: uuid.UUID
    evidence_cited: list[str] = Field(min_length=0)  # 0 allowed only on the ABSTAIN short-circuit
    outcome: DecisionOutcome
    reasoning_summary: str = Field(max_length=_REASONING_SUMMARY_MAX_LENGTH)
    recommendation: Optional[RecommendationType] = None
    recommendation_rationale: Optional[str] = Field(
        default=None, max_length=_RECOMMENDATION_RATIONALE_MAX_LENGTH
    )
    projection_narrative: Optional[str] = Field(
        default=None, max_length=_PROJECTION_NARRATIVE_MAX_LENGTH
    )
    event_classification: Optional[EventClassification] = None
    structured_report: Optional[EventReportSections] = None
    abstention_reason: Optional[str] = None
    # Propagated per Frozen Decisions — set by application code, never the
    # model. Deliberately absent from _LLMDecisionDraft (see module
    # docstring's structural guarantee).
    confidence: float = Field(ge=0.0, le=1.0)
    binding_constraint: str
    # Phase 18, Decision C: set ONLY by verification_service.py when
    # constructing a brand-new superseding ABSTAIN DecisionResult after a
    # failed verification — points at the ORIGINAL decision_id this one
    # supersedes. None on every Phase 17-produced decision and on every
    # non-superseding decision.
    superseded_decision_id: Optional[uuid.UUID] = None

    @model_validator(mode="after")
    def _validate_recommendation_null_when_inappropriate(self) -> "DecisionResult":
        # Decision #1: recommendation is null when outcome is NO_INCIDENT or
        # ABSTAIN; required (non-null) only for INCIDENT or WATCH.
        # recommendation_rationale always travels WITH recommendation:
        # required alongside it, null otherwise.
        appropriate_outcomes = (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH)
        if self.outcome in appropriate_outcomes:
            if self.recommendation is None:
                raise ValueError(
                    f"recommendation is required when outcome={self.outcome.value}"
                )
            if self.recommendation_rationale is None:
                raise ValueError(
                    f"recommendation_rationale is required when outcome={self.outcome.value}"
                )
        else:
            if self.recommendation is not None:
                raise ValueError(
                    f"recommendation must be null when outcome={self.outcome.value}"
                )
            if self.recommendation_rationale is not None:
                raise ValueError(
                    f"recommendation_rationale must be null when outcome={self.outcome.value}"
                )
        return self

    @model_validator(mode="after")
    def _validate_event_classification_null_when_inappropriate(self) -> "DecisionResult":
        # Acute-Hazard Trigger Phase (decision 4/6): same nullability shape
        # as recommendation above — required for INCIDENT/WATCH, null for
        # NO_INCIDENT/ABSTAIN.
        appropriate_outcomes = (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH)
        if self.outcome in appropriate_outcomes:
            if self.event_classification is None:
                raise ValueError(
                    f"event_classification is required when outcome={self.outcome.value}"
                )
        elif self.event_classification is not None:
            raise ValueError(
                f"event_classification must be null when outcome={self.outcome.value}"
            )
        return self

    @model_validator(mode="after")
    def _validate_structured_report_null_when_inappropriate(self) -> "DecisionResult":
        # Required only for INCIDENT (WATCH keeps the simpler existing
        # reasoning_summary only, per the developer's explicit choice).
        if self.outcome == DecisionOutcome.INCIDENT:
            if self.structured_report is None:
                raise ValueError("structured_report is required when outcome=INCIDENT")
        elif self.structured_report is not None:
            raise ValueError(
                f"structured_report must be null when outcome={self.outcome.value}"
            )
        return self

    @model_validator(mode="after")
    def _validate_abstention_reason(self) -> "DecisionResult":
        if self.outcome == DecisionOutcome.ABSTAIN:
            if self.abstention_reason is None:
                raise ValueError("abstention_reason is required when outcome=ABSTAIN")
        elif self.abstention_reason is not None:
            raise ValueError("abstention_reason must be null when outcome != ABSTAIN")
        return self
