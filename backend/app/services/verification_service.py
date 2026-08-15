"""Orchestration + persistence for Phase 18 verification. Implements
Decisions B/C/D end-to-end: calls `should_verify` (Decision D), calls
`Verifier.verify()` if warranted, ALWAYS persists a NEW verification_results
row (insert-only), and if verification failed, persists a NEW
decision_results row (outcome=ABSTAIN, superseded_decision_id set) via
Phase 17's EXISTING `decision_service.persist_decision_result` — reused
directly, not duplicated (Decision C).

`decision_results` (Phase 17) remains completely unchanged by this module
for any EXISTING row — every write here is either a fresh
verification_results INSERT or a fresh decision_results INSERT for a
brand-new row. See test_verification_persistence.py's source-level
immutability check.
"""

import uuid
from dataclasses import dataclass
from typing import Optional

from sqlalchemy.orm import Session

from app.models.decision import DecisionResultRow
from app.models.verification import VerificationResultRow
from app.pipeline.decision_result import DecisionOutcome, DecisionResult
from app.pipeline.evidence_package import EvidencePackageResult
from app.pipeline.verification_gate import should_verify
from app.pipeline.verification_result import VerificationResult
from app.pipeline.verifier import Verifier
from app.services import decision_service


@dataclass
class VerificationOutcome:
    applicable: bool
    verification_result: Optional[VerificationResult] = None
    superseding_decision: Optional[DecisionResultRow] = None


def _persist_verification_result(db: Session, result: VerificationResult) -> VerificationResultRow:
    row = VerificationResultRow(
        id=result.verification_id,
        decision_id=result.decision_id,
        passed=result.passed,
        confidence_consistency_ok=result.confidence_consistency_ok,
        evidence_grounding_existence_ok=result.evidence_grounding_existence_ok,
        llm_checks=result.llm_checks.model_dump(mode="json") if result.llm_checks is not None else None,
        issues_found=result.issues_found,
        superseding_decision_id=result.superseding_decision_id,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def run_verification_if_warranted(
    db: Session, verifier: Verifier, decision: DecisionResult, evidence_package: EvidencePackageResult
) -> VerificationOutcome:
    if not should_verify(decision):
        return VerificationOutcome(applicable=False)

    result = verifier.verify(decision, evidence_package)

    superseding_row: Optional[DecisionResultRow] = None
    if not result.passed:
        # Decision C: the ORIGINAL decision_results row is left EXACTLY as
        # it was — never touched. A SECOND, NEW row is inserted instead,
        # via the SAME persist_decision_result Phase 17 already built and
        # tested for immutability.
        superseding = DecisionResult(
            decision_id=uuid.uuid4(),
            evidence_package_id=decision.evidence_package_id,
            evidence_cited=[],
            outcome=DecisionOutcome.ABSTAIN,
            reasoning_summary=(
                f"Abstained: original decision {decision.decision_id} failed "
                f"verification (verification_id will be set below). Issues: "
                f"{'; '.join(result.issues_found)}"
            ),
            recommendation=None,
            recommendation_rationale=None,
            projection_narrative=None,
            abstention_reason=(
                f"verification_failed: original_decision_id={decision.decision_id}, "
                f"issues={result.issues_found}"
            ),
            confidence=evidence_package.confidence,
            binding_constraint=evidence_package.binding_constraint,
            superseded_decision_id=decision.decision_id,
        )
        superseding_row = decision_service.persist_decision_result(db, superseding)
        result.superseding_decision_id = superseding_row.id

    _persist_verification_result(db, result)

    return VerificationOutcome(
        applicable=True, verification_result=result, superseding_decision=superseding_row
    )
