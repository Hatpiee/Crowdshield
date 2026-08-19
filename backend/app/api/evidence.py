from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.core.stream_token import (
    EvidenceAccessTokenError,
    generate_evidence_access_token,
    validate_evidence_access_token,
)
from app.models.analysis_session import AnalysisSession
from app.models.user import User
from app.pipeline.evidence_builder import EvidenceBuilder
from app.schemas.evidence import EvidenceItemRead, EvidencePackageRead, RegionRead, SessionEvidenceRead
from app.services import evidence_service
from app.services.evidence_service import EvidencePackageWithItems

JPEG_CONTENT_TYPE = "image/jpeg"

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
        vision_observations_present=package.vision_observations_present,
        predictive_projection_snapshot=package.predictive_projection_snapshot,
        acute_hazard_signal_snapshot=package.acute_hazard_signal_snapshot,
        event_window=package.event_window,
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


@router.get("/evidence/{evidence_id}/access-token")
def get_evidence_access_token(
    evidence_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Phase 23, Step 2: same shape as videos.py's /stream-token and
    # heatmaps.py's /access-token — protected by normal Bearer auth,
    # obtains the short-lived token the two image routes below require.
    entry = evidence_service.get_evidence_package(db, evidence_id)
    if entry is None:
        raise _evidence_package_not_found()

    token = generate_evidence_access_token(evidence_id, current_user.id)
    return success_envelope(
        {
            "token": token,
            "expires_in_seconds": settings.EVIDENCE_TOKEN_EXPIRE_MINUTES * 60,
        }
    )


def _serve_evidence_image(db: Session, evidence_id: UUID, token: str | None, relative_path_attr: str):
    # Deliberately NOT protected by get_current_user — an <img> tag issues a
    # plain GET and cannot attach a custom Authorization header. Same
    # deliberate, narrow exception to the JSON-envelope convention already
    # established for the video/heatmap image routes: a plain HTTP status,
    # no envelope body, since an <img> tag can't parse one anyway.
    if token is None:
        raise HTTPException(status_code=401)
    try:
        validate_evidence_access_token(token, evidence_id)
    except EvidenceAccessTokenError:
        raise HTTPException(status_code=401)

    entry = evidence_service.get_evidence_package(db, evidence_id)
    if entry is None:
        raise HTTPException(status_code=404)

    # representative_frame_path/roi_crop_path (the real filesystem path
    # components) are used ONLY server-side here to locate the file — never
    # included in any response body, matching EvidencePackageRead's own
    # non-leaking discipline.
    relative_path = getattr(entry.package, relative_path_attr)
    file_path = EvidenceBuilder().get_storage_dir() / relative_path
    if not file_path.exists():
        raise HTTPException(status_code=404)

    return FileResponse(path=file_path, media_type=JPEG_CONTENT_TYPE)


@router.get("/evidence/{evidence_id}/frame-image")
def get_evidence_frame_image(evidence_id: UUID, token: str | None = None, db: Session = Depends(get_db)):
    return _serve_evidence_image(db, evidence_id, token, "representative_frame_path")


@router.get("/evidence/{evidence_id}/roi-image")
def get_evidence_roi_image(evidence_id: UUID, token: str | None = None, db: Session = Depends(get_db)):
    return _serve_evidence_image(db, evidence_id, token, "roi_crop_path")
