from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.user import User
from app.schemas.evidence import EvidenceItemRead, EvidencePackageRead, RegionRead, SessionEvidenceRead
from app.services import evidence_service
from app.services.evidence_service import EvidencePackageWithItems

# RESOLUTION 3: Incident (§19, roadmap Phase 18) does not exist yet, so
# §26's literal "GET /incidents/{id}/evidence" cannot be built. These two
# routes are a direct, reasoned extension of the SAME precedent already
# established twice in this project (Phase 12's GET /sessions/{id}/heatmaps,
# Phase 13's GET /sessions/{id}/risk) — a genuinely persisted, queryable
# resource gets its own session-scoped + id-scoped GET routes. No Evidence
# Graph route (§22) — that is an explicitly separate, later
# audit-visualization concern.
#
# Two distinct URL shapes ("/sessions/{id}/evidence" and "/evidence/{id}")
# live in one router (no shared prefix), mirroring how risk.py/heatmaps.py
# each own one shape — kept together here since both serve the same
# resource type.
router = APIRouter(tags=["evidence"])


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


def _evidence_package_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Evidence package not found")
    )


def _to_read(entry: EvidencePackageWithItems) -> EvidencePackageRead:
    package = entry.package
    return EvidencePackageRead(
        id=package.id,
        schema_version=package.schema_version,
        session_id=package.session_id,
        frame_number=package.frame_number,
        timestamp_seconds=package.timestamp_seconds,
        trigger_type=package.trigger_type,
        trigger_reason=package.trigger_reason,
        crowd_metrics_summary=package.crowd_metrics_summary,
        risk_state_snapshot=package.risk_state_snapshot,
        confidence=package.confidence,
        binding_constraint=package.binding_constraint,
        complete=package.complete,
        missing=package.missing_evidence,
        contradictions=package.contradictions,
        evidence_items=[
            EvidenceItemRead(
                id=item.id,
                observation_id=item.observation_id,
                category=item.category,
                confidence=item.confidence,
                evidence_type=item.evidence_type,
                description=item.description,
                region=RegionRead(
                    x_min=item.region_x_min,
                    y_min=item.region_y_min,
                    x_max=item.region_x_max,
                    y_max=item.region_y_max,
                ),
            )
            for item in entry.items
        ],
        created_at=package.created_at,
    )


@router.get("/sessions/{session_id}/evidence")
def list_session_evidence(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    entries = evidence_service.get_session_evidence_packages(db, session_id)
    response = SessionEvidenceRead(items=[_to_read(entry) for entry in entries])
    return success_envelope(response.model_dump(mode="json"))


@router.get("/evidence/{evidence_id}")
def get_evidence_package(
    evidence_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    entry = evidence_service.get_evidence_package(db, evidence_id)
    if entry is None:
        raise _evidence_package_not_found()

    return success_envelope(_to_read(entry).model_dump(mode="json"))
