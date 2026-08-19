from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.decision import DecisionResultRow
from app.models.user import User
from app.models.verification import VerificationResultRow
from app.schemas.decision import DecisionResultRead, SessionDecisionsRead, VerificationResultRead
from app.services import decision_service

# Same reasoned-exception pattern as Phase 12/13/16 (§26 doesn't explicitly
# name this route either — a genuinely persisted, queryable resource gets
# its own session-scoped + id-scoped GET routes). Documented in
# DECISIONS.md.
router = APIRouter(tags=["decisions"])


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


def _decision_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Decision result not found")
    )


def _verification_to_read(row: VerificationResultRow) -> VerificationResultRead:
    llm_checks = row.llm_checks or {}
    return VerificationResultRead(
        id=row.id,
        passed=row.passed,
        confidence_consistency_ok=row.confidence_consistency_ok,
        evidence_grounding_existence_ok=row.evidence_grounding_existence_ok,
        # None (not False) when the LLM was never called — the four
        # LLM-checked dimensions genuinely have no answer, per Decision A.
        reasoning_consistency_ok=llm_checks.get("reasoning_consistency_ok"),
        contradiction_handling_ok=llm_checks.get("contradiction_handling_ok"),
        recommendation_consistency_ok=llm_checks.get("recommendation_consistency_ok"),
        unsupported_claims_ok=(
            None if "unsupported_claims_found" not in llm_checks
            else not llm_checks["unsupported_claims_found"]
        ),
        issues_found=row.issues_found,
        superseding_decision_id=row.superseding_decision_id,
        created_at=row.created_at,
    )


def _decision_to_read(
    row: DecisionResultRow, verification_row: VerificationResultRow | None = None
) -> DecisionResultRead:
    return DecisionResultRead(
        id=row.id,
        evidence_package_id=row.evidence_package_id,
        evidence_cited=row.evidence_cited,
        outcome=row.outcome,
        reasoning_summary=row.reasoning_summary,
        recommendation=row.recommendation,
        recommendation_rationale=row.recommendation_rationale,
        projection_narrative=row.projection_narrative,
        event_classification=row.event_classification,
        structured_report=row.structured_report,
        abstention_reason=row.abstention_reason,
        confidence=row.confidence,
        binding_constraint=row.binding_constraint,
        superseded_decision_id=row.superseded_decision_id,
        verification=_verification_to_read(verification_row) if verification_row is not None else None,
        created_at=row.created_at,
    )


@router.get("/sessions/{session_id}/decisions")
def list_session_decisions(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    rows = decision_service.get_session_decisions(db, session_id)
    response = SessionDecisionsRead(items=[_decision_to_read(row) for row in rows])
    return success_envelope(response.model_dump(mode="json"))


@router.get("/decisions/{decision_id}")
def get_decision_result(
    decision_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    row = decision_service.get_decision_result(db, decision_id)
    if row is None:
        raise _decision_not_found()

    verification_row = decision_service.get_verification_for_decision(db, decision_id)
    return success_envelope(_decision_to_read(row, verification_row).model_dump(mode="json"))
