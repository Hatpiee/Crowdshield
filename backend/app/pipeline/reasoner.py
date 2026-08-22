"""Ollama-served Qwen3-8B Reasoner adapter — roadmap Phase 16 Part 1 (this
project's Phase 17), master spec §8/§17/§28.

============================================================
SCOPE: SINGLE-PASS, EVIDENCE-FIRST REASONING ONLY
============================================================
This is the Reasoner half of §18's Reasoner->Verifier design — the
severity-gated Verifier (high-severity re-check) is a SEPARATE,
immediately-following phase, not built here. `Reasoner.reason()` is a
single, stateless-per-invocation function call: no multi-agent framework,
no agent memory, no agent-to-agent conversation (§18, constitutional).

============================================================
ARCHITECTURAL NOTE — same "model residency is not this process's job" as
Phase 14's MiniCPMVisionModel
============================================================
`__init__` loads no model weights into this process — Ollama's own daemon
owns residency. `__init__` only constructs a lightweight HTTP client and
verifies (via a real `ollama.Client.list()` call) that LLM_MODEL is
actually pulled, failing fast with `LLMUnavailableError` at construction
time (§17: "LLM unavailable -> decision unavailable, but the Evidence
Package remains fully inspectable" — never silently deferred, never a
fabricated result).

============================================================
EMPIRICALLY VERIFIED (this phase) — Qwen3-8B thinking mode + format=schema
============================================================
Confirmed via a direct probe against the actually-pulled `qwen3:8b` tag,
NOT assumed from Phase 14's MiniCPM-V precedent: `think` is a genuine
runtime-toggleable bool on this SINGLE tag (no separate "-thinking"
suffixed tag needed for the classic Qwen3 dense sizes). With
`think=False`, `response.message.thinking` is `None` and `content` is the
clean schema-conforming JSON. With `think=True`, `thinking` arrives as a
real, separate field carrying genuine chain-of-thought text, and `content`
remains valid per the schema in both cases. This phase's Reasoner always
uses `think=False` (fast mode, ~36s vs ~62s measured latency for `think=True`
in the same probe) — the severity-gated Verifier (a later phase) is where
`think=True` is expected to matter.
"""

import dataclasses
import json
import logging
import time
import uuid

import httpx
import ollama
from pydantic import ValidationError

from app.core.config import settings
from app.pipeline.abstention import should_abstain
from app.pipeline.decision_result import (
    DecisionOutcome,
    DecisionResult,
    EventClassification,
    _LLMDecisionDraft,
)
from app.pipeline.evidence_package import EvidencePackageResult

logger = logging.getLogger(__name__)


class LLMUnavailableError(Exception):
    """Ollama is unreachable, times out, or the configured model isn't
    pulled — per §17's failure-handling requirement, this must NEVER be
    silently swallowed into a fabricated DecisionResult."""


class LLMResponseValidationError(Exception):
    """The model's response failed schema OR business-rule (decision #1's
    recommendation-nullability) validation even after LLM_MAX_RETRIES
    retries — never silently return a fabricated or partial result."""


_UNAVAILABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    httpx.TimeoutException,
    httpx.ConnectError,
    ollama.RequestError,
    ollama.ResponseError,
)

# Decisions #5/#6 combined into one system-level instruction sent on every
# non-abstained request.
SYSTEM_PROMPT = (
    "You are a crowd-safety decision assistant supporting human operators. "
    "You will be given a structured Evidence Package (already-computed "
    "crowd metrics, risk state, and vision observations) — you do NOT see "
    "any raw video or images.\n\n"
    "GROUND-TRUTH RULE (mandatory): treat every numeric value provided "
    "(risk_score, pressure, density, congested_cell_fraction, etc.) as "
    "ESTABLISHED GROUND TRUTH. NEVER recompute, second-guess, override, or "
    "contradict these numbers — only reason about their IMPLICATIONS. You "
    "are not an analytics engine: you do not calculate Crowd Pressure, do "
    "not perform detection, and do not independently invent quantitative "
    "metrics.\n\n"
    "EVIDENCE-FIRST RULE (mandatory): populate evidence_cited with SPECIFIC "
    "references — either an exact compact_metrics field name (e.g. "
    "'risk_score', 'congested_cell_fraction') or an exact vision "
    "observation_id from the evidence provided — never vague paraphrasing. "
    "Cite ONLY evidence actually present in the input.\n\n"
    "OUTCOME RULE: choose exactly one of INCIDENT, WATCH, NO_INCIDENT based "
    "on what the cited evidence actually shows.\n\n"
    "ACUTE-HAZARD EVIDENCE WEIGHING RULE (mandatory when trigger_reason "
    "indicates an acute-hazard trigger): an ACUTE_HAZARD trigger means "
    "ONLY 'worth semantic inspection' — it is NOT itself evidence that an "
    "incident occurred. Prefer WATCH over INCIDENT when the visual "
    "evidence for this specific event is thin (e.g. a single brief, "
    "generic, or ambiguous observation; no clear description of a "
    "distinct hazardous event; wording that itself sounds uncertain, like "
    "'suggests' or 'could be interpreted as'). Reserve INCIDENT for cases "
    "where the vision observation(s) clearly and specifically describe an "
    "acute event (e.g. an explosion, fire, structural collapse, or "
    "similarly distinct hazardous occurrence), not merely a routine "
    "crowd-density/bottleneck/obstruction description that could equally "
    "describe an ordinary, unremarkable scene.\n\n"
    "RECOMMENDATION RULE: when outcome is INCIDENT or WATCH, you MUST "
    "provide a recommendation (from the fixed allowed set) and a "
    "recommendation_rationale explaining why. When outcome is NO_INCIDENT, "
    "leave recommendation and recommendation_rationale null.\n\n"
    "PROJECTION RULE: if a predictive_projection is included in the "
    "evidence, put its ALREADY-COMPUTED numbers (projected_pressure, "
    "horizon_seconds, r_squared) into plain language in "
    "projection_narrative — you must NOT generate your own numeric "
    "prediction or restate different numbers. If no predictive_projection "
    "is included in the evidence, leave projection_narrative null.\n\n"
    "EVENT CLASSIFICATION RULE: when outcome is INCIDENT or WATCH, set "
    "event_classification to the single BEST-FITTING category describing "
    "what kind of acute event the evidence shows — not every incident is a "
    "crowd crush. If the evidence shows dense-crowd pressure/congestion "
    "dynamics with no other acute event visible, classify it CROWD_CRUSH. "
    "If the evidence (especially any acute_hazard_signal or vision "
    "observations) instead points to a sudden explosion/blast, classify "
    "EXPLOSIVE_EVENT; visible fire or smoke without a clear explosion, "
    "FIRE_OR_SMOKE; a vehicle-involved event, VEHICLE_INCIDENT; a collapsing "
    "or failing structure, STRUCTURAL_HAZARD; a blocking obstruction or "
    "barrier, OBSTRUCTION_OR_BARRIER; sudden crowd dispersal/scattering "
    "without a clear cause, STAMPEDE_LIKE_DISPERSAL; a real acute event "
    "that fits none of these, OTHER_ACUTE_HAZARD; and if the evidence "
    "genuinely does not support confidently picking any category, UNKNOWN "
    "— never guess a specific category the evidence does not support. "
    "Leave event_classification null when outcome is NO_INCIDENT.\n\n"
    "STRUCTURED REPORT RULE: when outcome is INCIDENT, you MUST populate "
    "structured_report with all six of its fields, each grounded ONLY in "
    "evidence actually provided: event_summary (plain-language summary of "
    "what happened), observed_evidence (an itemized list of concrete, "
    "evidence-supported observations — never invent an item the evidence "
    "does not show), behavioral_analysis (how nearby people/vehicles "
    "behaved — prefer measurable descriptions like 'rapid directional "
    "change' over loaded words like 'panic' unless the evidence actually "
    "supports that specific word), spatial_analysis (where the event "
    "occurred and whether it affected one region or spread), "
    "temporal_analysis (baseline/onset/aftermath, using the "
    "event_window evidence field if present — if it only gives an "
    "onset WINDOW rather than an exact instant, say so as a window, never "
    "invent a precise onset time it does not support), and "
    "crowd_risk_context (explicitly state the numeric risk_score/risk_state "
    "from compact_metrics, and separately state whether the acute event's "
    "own severity is fully explained by that number or not — e.g. "
    "'the crowd-risk score remained in the NORMAL range; independent acute-"
    "hazard evidence indicates a severe event the crowd-risk score alone "
    "does not capture'). When outcome is WATCH, structured_report is "
    "OPTIONAL: populate it the SAME way (all six fields, same grounding "
    "rules) ONLY if you genuinely have enough concrete evidence to fill "
    "every field meaningfully; otherwise leave the entire structured_report "
    "null and rely on reasoning_summary alone — never populate only some "
    "fields, and never pad or invent content just to satisfy all six "
    "fields on thin evidence. Leave structured_report null when outcome is "
    "NO_INCIDENT.\n\n"
    "CONCISENESS RULE (mandatory, applies to every field above and to "
    "reasoning_summary/recommendation_rationale): be TERSE. Each "
    "structured_report field is ONE TO TWO SHORT SENTENCES, never a "
    "paragraph. observed_evidence items are SHORT PHRASES (a few words "
    "each), not sentences, and at most 6 items. NEVER repeat the same "
    "fact, sentence, or wording across two different fields — each field "
    "covers ONLY its own named topic (event_summary is not a place to "
    "restate behavioral_analysis, crowd_risk_context is not a place to "
    "restate observed_evidence, etc.). State the fact plainly once; do not "
    "pad, hedge repeatedly, or restate the same qualifier in multiple "
    "sentences.\n\n"
    "LANGUAGE SAFETY RULE (mandatory): distinguish OBSERVABLE facts (what "
    "the evidence directly shows — e.g. 'a sudden explosive-looking blast, "
    "expanding smoke/dust, debris/disruption, abrupt crowd/vehicle "
    "reaction') from INFERRED/SUSPECTED event class (a reasonable "
    "interpretation — e.g. 'consistent with an apparent explosive incident "
    "/ possible bombing') from UNDETERMINABLE details you must NEVER claim "
    "(exact explosive mechanism, attacker identity, device origin, intent, "
    "or a specific attack method such as 'suicide bombing'). You have no "
    "forensic ground truth from video evidence alone — do not state or "
    "imply you do. When uncertain, say so plainly rather than sounding "
    "more certain than the evidence supports.\n\n"
    "Respond ONLY with JSON matching the provided schema."
)


def _serialize_evidence_context(evidence_package: EvidencePackageResult) -> str:
    observation_payloads = [
        {
            "observation_id": str(obs.observation_id),
            "category": obs.category.value,
            "description": obs.description,
            "confidence": obs.confidence,
            "evidence_type": obs.evidence_type.value,
        }
        for obs in evidence_package.vision_observations
    ]
    projection_payload = None
    if evidence_package.predictive_projection_snapshot is not None:
        projection_payload = dataclasses.asdict(evidence_package.predictive_projection_snapshot)

    # Acute-Hazard Trigger Phase: both None for every RISK/FALLBACK/OPERATOR
    # package — only populated for ACUTE_HAZARD, same pattern as
    # predictive_projection_snapshot above.
    acute_hazard_payload = None
    if evidence_package.acute_hazard_signal_snapshot is not None:
        acute_hazard_payload = dataclasses.asdict(evidence_package.acute_hazard_signal_snapshot)
    event_window_payload = None
    if evidence_package.event_window is not None:
        event_window_payload = dataclasses.asdict(evidence_package.event_window)

    payload = {
        "trigger_reason": evidence_package.trigger_reason,
        "compact_metrics": evidence_package.crowd_metrics_summary.model_dump(mode="json"),
        "risk_state": dataclasses.asdict(evidence_package.risk_state_snapshot),
        "vision_observations": observation_payloads,
        "completeness": {
            "complete": evidence_package.complete,
            "missing": evidence_package.missing,
        },
        "predictive_projection": projection_payload,
        "acute_hazard_signal": acute_hazard_payload,
        "event_window": event_window_payload,
        "valid_evidence_citation_keys": (
            list(evidence_package.crowd_metrics_summary.model_dump(mode="json").keys())
            + [str(obs.observation_id) for obs in evidence_package.vision_observations]
        ),
    }
    return json.dumps(payload)


class Reasoner:
    """STATELESS — no cross-call state (unlike Tracker/BottleneckDetector),
    matching Phase 8's OpticalFlow statelessness pattern. Each `reason()`
    call is independent; construct once and reuse freely, or construct
    fresh per call — either is safe."""

    def __init__(self) -> None:
        self._model = settings.LLM_MODEL
        self._client = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.LLM_REQUEST_TIMEOUT_SECONDS
        )
        try:
            pulled = self._client.list()
        except _UNAVAILABLE_EXCEPTIONS as exc:
            raise LLMUnavailableError(
                f"Could not reach Ollama at {settings.OLLAMA_BASE_URL} to "
                f"verify LLM_MODEL={self._model!r} is pulled: {exc}"
            ) from exc

        pulled_tags = {model.model for model in pulled.models}
        if self._model not in pulled_tags:
            raise LLMUnavailableError(
                f"LLM_MODEL={self._model!r} is not pulled in this Ollama "
                f"instance (pulled tags: {sorted(pulled_tags)}). Run: "
                f"ollama pull {self._model}"
            )

    def reason(self, evidence_package: EvidencePackageResult) -> DecisionResult:
        abstain_reason = should_abstain(evidence_package)
        if abstain_reason is not None:
            # Key Architectural Decision: abstention is deterministic —
            # construct and return WITHOUT calling the LLM at all.
            return DecisionResult(
                decision_id=uuid.uuid4(),
                evidence_package_id=evidence_package.package_id,
                # No LLM call means no genuine citations were produced —
                # an empty list is the honest representation (documented
                # judgment call, not asking the LLM for a placeholder).
                evidence_cited=[],
                outcome=DecisionOutcome.ABSTAIN,
                reasoning_summary=f"Abstained without LLM invocation: {abstain_reason}",
                recommendation=None,
                recommendation_rationale=None,
                projection_narrative=None,
                abstention_reason=abstain_reason,
                confidence=evidence_package.confidence,
                binding_constraint=evidence_package.binding_constraint,
            )

        evidence_context = _serialize_evidence_context(evidence_package)
        user_content = (
            "Evidence Package (already-computed by other system components — "
            "informational context only; do not recompute, contradict, or "
            f"restate these as your own measurement):\n{evidence_context}"
        )

        schema = _LLMDecisionDraft.model_json_schema()
        max_attempts = settings.LLM_MAX_RETRIES + 1
        last_error: Exception | None = None

        # Final CPU Stabilization phase: see minicpm_vlm.py's identical
        # pattern and config.py's OLLAMA_NUM_THREAD docstring.
        chat_options: dict = {
            "temperature": settings.LLM_TEMPERATURE,
            "num_predict": settings.LLM_MAX_GENERATION_TOKENS,
        }
        if settings.OLLAMA_NUM_THREAD is not None:
            chat_options["num_thread"] = settings.OLLAMA_NUM_THREAD

        for attempt in range(1, max_attempts + 1):
            start = time.perf_counter()
            try:
                response = self._client.chat(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": SYSTEM_PROMPT},
                        {"role": "user", "content": user_content},
                    ],
                    format=schema,
                    options=chat_options,
                    think=False,
                )
            except _UNAVAILABLE_EXCEPTIONS as exc:
                raise LLMUnavailableError(
                    f"Ollama at {settings.OLLAMA_BASE_URL} unavailable "
                    f"during reason(): {exc}"
                ) from exc
            latency_seconds = time.perf_counter() - start
            logger.debug("Reasoner: attempt %d/%d took %.2fs", attempt, max_attempts, latency_seconds)

            try:
                draft = _LLMDecisionDraft.model_validate_json(response.message.content or "")
            except ValidationError as exc:
                last_error = exc
                logger.warning(
                    "Reasoner: response failed schema validation on attempt "
                    "%d/%d: %s", attempt, max_attempts, exc,
                )
                continue

            # Decision #5, defensive re-validation (never trust the model to
            # leave projection_narrative null on its own, even though the
            # system prompt instructs it to): force null whenever no
            # projection snapshot was actually provided as input.
            projection_narrative = draft.projection_narrative
            if evidence_package.predictive_projection_snapshot is None:
                projection_narrative = None

            # Reasoner Stability phase — ROOT CAUSE FOUND DURING STEP 0/1
            # RE-READ (not merely a latency issue): this call site NEVER
            # actually passed draft.event_classification/draft.structured_
            # report into DecisionResult() below — both silently kept their
            # Pydantic defaults of None. Since DecisionResult's own
            # `_validate_event_classification_null_when_inappropriate`
            # validator REQUIRES a non-null value whenever outcome is
            # INCIDENT/WATCH, this meant EVERY SINGLE INCIDENT/WATCH-outcome
            # response — regardless of what the model actually generated —
            # deterministically failed business-rule validation and
            # retried, unconditionally burning 1-2 extra ~100s+ real LLM
            # calls on every such decision (this is what was compounding
            # with per-call latency variance to blow past
            # LLM_REQUEST_TIMEOUT_SECONDS on the two real-inference crisis-
            # case tests). Fixed below by actually wiring the drafted
            # fields through.
            #
            # Separately (a real, if secondary, model-behavior gap):  the
            # model can still genuinely omit event_classification even
            # though it is schema-optional/prompt-mandated. A missing
            # classification is defaulted to UNKNOWN — an HONEST "evidence
            # did not support confidently classifying this" statement (an
            # actual value in the taxonomy, not a placeholder) — rather
            # than retrying for it, since another ~100s+ call is not
            # guaranteed to fix a stochastic omission and Step 5 explicitly
            # forbids using a second LLM call to repair the first.
            event_classification = draft.event_classification
            if (
                draft.outcome in (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH)
                and event_classification is None
            ):
                event_classification = EventClassification.UNKNOWN
                logger.warning(
                    "Reasoner: model omitted event_classification for "
                    "outcome=%s on attempt %d/%d — deterministically "
                    "defaulting to UNKNOWN rather than retrying (never "
                    "invented, never re-queried).",
                    draft.outcome.value, attempt, max_attempts,
                )

            try:
                return DecisionResult(
                    decision_id=uuid.uuid4(),
                    evidence_package_id=evidence_package.package_id,
                    evidence_cited=draft.evidence_cited,
                    outcome=draft.outcome,
                    reasoning_summary=draft.reasoning_summary,
                    recommendation=draft.recommendation,
                    recommendation_rationale=draft.recommendation_rationale,
                    projection_narrative=projection_narrative,
                    event_classification=event_classification,
                    structured_report=draft.structured_report,
                    abstention_reason=None,
                    # Frozen Decision: NEVER invented — propagated directly
                    # from the EvidencePackage's already-computed value. The
                    # LLM's own draft schema has no confidence field to
                    # fill in at all (structural guarantee, see
                    # decision_result.py).
                    confidence=evidence_package.confidence,
                    binding_constraint=evidence_package.binding_constraint,
                )
            except ValidationError as exc:
                # Business-rule validation (decision #1's null-when-
                # inappropriate rule) failed — same retry treatment as a
                # schema validation failure.
                last_error = exc
                logger.warning(
                    "Reasoner: response failed business-rule validation on "
                    "attempt %d/%d: %s", attempt, max_attempts, exc,
                )
                continue

        raise LLMResponseValidationError(
            f"Qwen3-8B response failed validation after {max_attempts} "
            f"attempts: {last_error}"
        )
