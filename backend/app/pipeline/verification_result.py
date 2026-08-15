"""Verification data contract — roadmap Phase 16 Part 2 (this project's
Phase 18), §18/§28. `Verifier.verify(decision, evidence_package) ->
VerificationResult` is the sole entry point (verifier.py); this module
holds only the data contract.

§18 names six checks: evidence grounding, reasoning consistency,
contradiction handling, recommendation consistency, confidence consistency,
unsupported claims. Per Decision A, TWO are fully deterministic
(confidence_consistency, and the EXISTENCE portion of evidence_grounding)
and checked in plain Python BEFORE any LLM call (verification_prechecks.py)
— the remaining FOUR genuinely require semantic judgment and are the only
ones asked of the LLM.
"""

import uuid
from dataclasses import dataclass, field
from typing import Literal, Optional

from pydantic import BaseModel, Field

_NOTE_MAX_LENGTH = 500


class _LLMVerificationDraft(BaseModel):
    """The ONLY schema sent to Ollama's `format` parameter for the deep-
    reasoning (`think=True`) verification pass. Covers exactly the FOUR
    checks that require semantic judgment — reasoning_consistency,
    contradiction_handling, recommendation_consistency, unsupported_claims
    — never confidence_consistency or evidence_grounding-existence (those
    are deterministic, per Decision A, and never even reach this schema)."""

    reasoning_consistency_ok: bool
    reasoning_consistency_note: str = Field(max_length=_NOTE_MAX_LENGTH)
    contradiction_handling_ok: bool
    contradiction_handling_note: str = Field(max_length=_NOTE_MAX_LENGTH)
    recommendation_consistency_ok: bool
    recommendation_consistency_note: str = Field(max_length=_NOTE_MAX_LENGTH)
    unsupported_claims_found: bool
    unsupported_claims_note: str = Field(max_length=_NOTE_MAX_LENGTH)
    overall_verdict: Literal["CONFIRMED", "FLAGGED"]


@dataclass
class VerificationResult:
    verification_id: uuid.UUID
    decision_id: uuid.UUID
    passed: bool
    confidence_consistency_ok: bool
    evidence_grounding_existence_ok: bool
    # None when deterministically short-circuited (Decision A) — the LLM
    # was never called at all, so there is genuinely no LLM-checks payload,
    # not an empty/default one.
    llm_checks: Optional[_LLMVerificationDraft] = None
    issues_found: list[str] = field(default_factory=list)
    # Set only when passed=False AND a new superseding ABSTAIN decision was
    # actually created (verification_service.py, Decision C) — None on a
    # passing VerificationResult, and None if this VerificationResult is
    # returned before persistence has happened at all (Verifier.verify()
    # itself never sets this; only the persistence layer does).
    superseding_decision_id: Optional[uuid.UUID] = None
