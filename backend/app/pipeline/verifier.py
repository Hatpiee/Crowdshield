"""Ollama-served Qwen3-8B Verifier adapter — roadmap Phase 16 Part 2 (this
project's Phase 18), master spec §18/§28.

============================================================
SCOPE: SEVERITY-GATED SECOND PASS, DEEP REASONING ONLY
============================================================
Completes §18's Reasoner->Verifier design (Phase 17 built the Reasoner).
Severity gating (should_verify — verification_gate.py) lives in the
CALLER, not here (Decision D) — `Verifier.verify()` always runs both
deterministic pre-checks and, if warranted, a real `think=True` LLM call;
it never checks `decision.outcome` itself. Stateless per invocation (§8) —
no cross-call state, same as Reasoner.

============================================================
STEP 0 — REAL LATENCY MEASUREMENT (this project's own discipline: never
guess a timeout, measure it)
============================================================
The prompt-construction logic below (`_build_verification_prompt`) was
written and used FIRST, in isolation, to run 5 real `think=True` Qwen3-8B
calls against a REAL, already-persisted Phase 17 EvidencePackage +
DecisionResult pair BEFORE the rest of this module (or any other Phase 18
code) was written — see DECISIONS.md's "Step 0: Real Verifier Latency
Measurement" entry for the full methodology, the 5 raw measurements, and
the resulting `VERIFIER_REQUEST_TIMEOUT_SECONDS` calculation.
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
from app.pipeline.decision_result import DecisionResult
from app.pipeline.evidence_package import EvidencePackageResult
from app.pipeline.verification_prechecks import (
    check_confidence_consistency,
    check_evidence_grounding_existence,
)
from app.pipeline.verification_result import _LLMVerificationDraft, VerificationResult

logger = logging.getLogger(__name__)


class LLMVerificationUnavailableError(Exception):
    """Ollama is unreachable, times out, or the configured model isn't
    pulled — same failure-handling discipline as Phase 17's
    LLMUnavailableError: never silently swallowed into a fabricated
    VerificationResult."""


class LLMVerificationResponseValidationError(Exception):
    """The model's response failed schema validation even after
    VERIFIER_MAX_RETRIES retries."""


_UNAVAILABLE_EXCEPTIONS = (
    ConnectionError,
    TimeoutError,
    httpx.TimeoutException,
    httpx.ConnectError,
    ollama.RequestError,
    ollama.ResponseError,
)

# Only the FOUR checks that genuinely require semantic judgment (Decision
# A) — confidence_consistency and evidence_grounding-existence are
# deterministic and never mentioned to the model at all.
VERIFICATION_SYSTEM_PROMPT = (
    "You are a second-pass safety verifier reviewing another AI system's "
    "crowd-safety decision before it is acted on. You will be given the "
    "ORIGINAL EVIDENCE (already-computed metrics and vision observations) "
    "and the DECISION that was produced from it (outcome, "
    "reasoning_summary, recommendation, evidence_cited). Your job is to "
    "independently check FOUR specific things — do NOT re-derive the "
    "outcome yourself, do NOT recompute any metric, only assess whether "
    "the decision's OWN reasoning holds up against the evidence actually "
    "given:\n\n"
    "1. reasoning_consistency: does reasoning_summary logically follow "
    "from the cited evidence, without internal contradiction?\n"
    "2. contradiction_handling: if the evidence contains any flagged "
    "contradictions, did the decision appropriately account for them "
    "(not ignore them)?\n"
    "3. recommendation_consistency: if a recommendation was made, is it a "
    "reasonable, evidence-supported response to the stated outcome and "
    "reasoning (not arbitrary or mismatched)?\n"
    "4. unsupported_claims: does reasoning_summary, recommendation_rationale, "
    "or projection_narrative assert anything (a fact, a number, a claim) "
    "that is NOT actually traceable to the evidence provided? Flag ANY "
    "such claim, however plausible-sounding.\n\n"
    "Set overall_verdict to \"CONFIRMED\" only if ALL FOUR checks pass; "
    "otherwise \"FLAGGED\". Respond ONLY with JSON matching the provided "
    "schema."
)


def _build_verification_prompt(decision: DecisionResult, evidence_package: EvidencePackageResult) -> str:
    """Isolated, reusable prompt-construction logic — written and used
    FIRST (Step 0) to measure real latency before the rest of the Verifier
    class existed. Deliberately mirrors reasoner.py's
    `_serialize_evidence_context` shape for the evidence half, plus the
    decision's own fields, so the model sees BOTH."""
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

    evidence_payload = {
        "trigger_reason": evidence_package.trigger_reason,
        "compact_metrics": evidence_package.crowd_metrics_summary.model_dump(mode="json"),
        "risk_state": dataclasses.asdict(evidence_package.risk_state_snapshot),
        "vision_observations": observation_payloads,
        "completeness": {
            "complete": evidence_package.complete,
            "missing": evidence_package.missing,
        },
        "contradictions": [dataclasses.asdict(c) for c in evidence_package.contradictions],
        "predictive_projection": projection_payload,
    }
    decision_payload = {
        "evidence_cited": decision.evidence_cited,
        "outcome": decision.outcome.value,
        "reasoning_summary": decision.reasoning_summary,
        "recommendation": decision.recommendation.value if decision.recommendation else None,
        "recommendation_rationale": decision.recommendation_rationale,
        "projection_narrative": decision.projection_narrative,
    }

    payload = {"original_evidence": evidence_payload, "decision_to_verify": decision_payload}
    return json.dumps(payload)


class Verifier:
    """STATELESS — no cross-call state (§8), constructed once or per call,
    either is safe. Mirrors Reasoner's own construction pattern: verifies
    (via a real `ollama.Client.list()` call) that LLM_MODEL is pulled,
    failing fast with LLMVerificationUnavailableError at construction time.
    Uses the SAME Qwen3-8B model as the Reasoner (§8/§17/§40 name one LLM,
    not two) — only the `think` mode and prompt differ."""

    def __init__(self) -> None:
        self._model = settings.LLM_MODEL
        self._client = ollama.Client(
            host=settings.OLLAMA_BASE_URL, timeout=settings.VERIFIER_REQUEST_TIMEOUT_SECONDS
        )
        try:
            pulled = self._client.list()
        except _UNAVAILABLE_EXCEPTIONS as exc:
            raise LLMVerificationUnavailableError(
                f"Could not reach Ollama at {settings.OLLAMA_BASE_URL} to "
                f"verify LLM_MODEL={self._model!r} is pulled: {exc}"
            ) from exc

        pulled_tags = {model.model for model in pulled.models}
        if self._model not in pulled_tags:
            raise LLMVerificationUnavailableError(
                f"LLM_MODEL={self._model!r} is not pulled in this Ollama "
                f"instance (pulled tags: {sorted(pulled_tags)}). Run: "
                f"ollama pull {self._model}"
            )

    def verify(
        self, decision: DecisionResult, evidence_package: EvidencePackageResult
    ) -> VerificationResult:
        verification_id = uuid.uuid4()

        # Decision A: BOTH deterministic pre-checks run FIRST, in plain
        # Python. If EITHER fails, short-circuit WITHOUT calling the LLM.
        confidence_ok = check_confidence_consistency(decision, evidence_package)
        grounding_ok = check_evidence_grounding_existence(decision, evidence_package)

        if not confidence_ok or not grounding_ok:
            issues: list[str] = []
            if not confidence_ok:
                # Per Decision A: a confidence-propagation mismatch is a
                # REAL BUG in Phase 17's code (confidence is supposed to be
                # copied verbatim, never recomputed), not a model behavior
                # issue — flagged explicitly and distinctly so it is never
                # mistaken for an LLM-judgment disagreement.
                issues.append(
                    "CONFIDENCE CONSISTENCY FAILURE (indicates a Phase 17 "
                    "propagation BUG, not model behavior): "
                    f"decision.confidence={decision.confidence} != "
                    f"evidence_package.confidence={evidence_package.confidence}"
                )
            if not grounding_ok:
                invalid = sorted(
                    set(decision.evidence_cited)
                    - {
                        *evidence_package.crowd_metrics_summary.model_dump(mode="json").keys(),
                        *(str(o.observation_id) for o in evidence_package.vision_observations),
                    }
                )
                issues.append(
                    "EVIDENCE GROUNDING FAILURE: decision.evidence_cited contains "
                    f"citation(s) with no corresponding compact_metrics field or "
                    f"vision observation_id in the evidence package: {invalid}"
                )
            return VerificationResult(
                verification_id=verification_id,
                decision_id=decision.decision_id,
                passed=False,
                confidence_consistency_ok=confidence_ok,
                evidence_grounding_existence_ok=grounding_ok,
                llm_checks=None,
                issues_found=issues,
            )

        # Both deterministic checks passed — proceed to the genuine
        # semantic-judgment pass (think=True, per the Reasoner(fast)/
        # Verifier(deep) mapping established in Phase 17).
        prompt = _build_verification_prompt(decision, evidence_package)
        schema = _LLMVerificationDraft.model_json_schema()
        max_attempts = settings.VERIFIER_MAX_RETRIES + 1
        last_error: Exception | None = None

        for attempt in range(1, max_attempts + 1):
            start = time.perf_counter()
            try:
                response = self._client.chat(
                    model=self._model,
                    messages=[
                        {"role": "system", "content": VERIFICATION_SYSTEM_PROMPT},
                        {"role": "user", "content": prompt},
                    ],
                    format=schema,
                    options={
                        "temperature": settings.LLM_TEMPERATURE,
                        "num_predict": settings.VERIFIER_MAX_THINKING_TOKENS,
                    },
                    think=True,
                )
            except _UNAVAILABLE_EXCEPTIONS as exc:
                raise LLMVerificationUnavailableError(
                    f"Ollama at {settings.OLLAMA_BASE_URL} unavailable "
                    f"during verify(): {exc}"
                ) from exc
            latency_seconds = time.perf_counter() - start
            logger.debug(
                "Verifier: attempt %d/%d took %.2fs", attempt, max_attempts, latency_seconds
            )

            try:
                draft = _LLMVerificationDraft.model_validate_json(response.message.content or "")
            except ValidationError as exc:
                last_error = exc
                logger.warning(
                    "Verifier: response failed schema validation on attempt "
                    "%d/%d: %s", attempt, max_attempts, exc,
                )
                continue

            issues = []
            if not draft.reasoning_consistency_ok:
                issues.append(f"reasoning_consistency: {draft.reasoning_consistency_note}")
            if not draft.contradiction_handling_ok:
                issues.append(f"contradiction_handling: {draft.contradiction_handling_note}")
            if not draft.recommendation_consistency_ok:
                issues.append(f"recommendation_consistency: {draft.recommendation_consistency_note}")
            if draft.unsupported_claims_found:
                issues.append(f"unsupported_claims: {draft.unsupported_claims_note}")

            passed = (
                draft.overall_verdict == "CONFIRMED"
                and draft.reasoning_consistency_ok
                and draft.contradiction_handling_ok
                and draft.recommendation_consistency_ok
                and not draft.unsupported_claims_found
            )

            return VerificationResult(
                verification_id=verification_id,
                decision_id=decision.decision_id,
                passed=passed,
                confidence_consistency_ok=True,
                evidence_grounding_existence_ok=True,
                llm_checks=draft,
                issues_found=issues,
            )

        raise LLMVerificationResponseValidationError(
            f"Qwen3-8B verification response failed schema validation after "
            f"{max_attempts} attempts: {last_error}"
        )
