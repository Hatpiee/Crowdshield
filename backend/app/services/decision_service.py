"""Persistence layer for DecisionResult (Phase 17). Mirrors Phase 16's
evidence_service.py pattern exactly: this module is the ONLY place a
DecisionResultRow gets written.

RESOLUTION (immutability is an application-layer guarantee, same as Phase
16's Resolution 4): there is DELIBERATELY no update/modify function
anywhere in this module for an already-persisted decision_results row.
"""

import uuid

from sqlalchemy.orm import Session

from app.models.decision import DecisionResultRow
from app.models.evidence import EvidencePackage
from app.pipeline.decision_result import DecisionResult


def persist_decision_result(db: Session, result: DecisionResult) -> DecisionResultRow:
    row = DecisionResultRow(
        id=result.decision_id,
        evidence_package_id=result.evidence_package_id,
        evidence_cited=result.evidence_cited,
        outcome=result.outcome,
        reasoning_summary=result.reasoning_summary,
        recommendation=result.recommendation,
        recommendation_rationale=result.recommendation_rationale,
        projection_narrative=result.projection_narrative,
        abstention_reason=result.abstention_reason,
        confidence=result.confidence,
        binding_constraint=result.binding_constraint,
    )
    db.add(row)
    db.commit()
    db.refresh(row)
    return row


def get_session_decisions(db: Session, session_id: uuid.UUID) -> list[DecisionResultRow]:
    return (
        db.query(DecisionResultRow)
        .join(EvidencePackage, DecisionResultRow.evidence_package_id == EvidencePackage.id)
        .filter(EvidencePackage.session_id == session_id)
        .order_by(DecisionResultRow.created_at.desc())
        .all()
    )


def get_decision_result(db: Session, decision_id: uuid.UUID) -> DecisionResultRow | None:
    return db.get(DecisionResultRow, decision_id)
