from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.core.config import settings
from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.core.stream_token import (
    HeatmapAccessTokenError,
    generate_heatmap_access_token,
    validate_heatmap_access_token,
)
from app.models.analysis_session import AnalysisSession
from app.models.heatmap import HeatmapSnapshot, HeatmapType
from app.models.user import User
from app.schemas.heatmap import HeatmapSnapshotListResponse, HeatmapSnapshotRead
from app.services.heatmap_service import get_storage_dir

JPEG_CONTENT_TYPE = "image/jpeg"

# Mounted under the SAME "/sessions" prefix as app/api/sessions.py (Phase
# 4) — a separate router (rather than adding routes directly to
# sessions.py) keeps this phase's genuinely new, persisted-resource
# surface (Resolution 3) cleanly separated in its own module, while still
# nesting under /sessions/{session_id}/... per §26's frozen route shape.
#
# READ-ONLY / METADATA-ONLY (Resolution 3, Phase 12): these routes never
# serve raw image bytes and never trigger generation on demand — they only
# query already-persisted HeatmapSnapshot rows. Actual byte-serving was
# deferred to the dashboard integration phase and is now provided below by
# the SEPARATE `media_router` (Phase 22, Step 2), mirroring video
# streaming's own Phase 3-defer/Phase 21-close pattern exactly.
router = APIRouter(prefix="/sessions", tags=["heatmaps"])

# Phase 22, Step 2: a heatmap snapshot is looked up by its OWN id, not
# nested under /sessions/{session_id}/..., so this is a genuinely separate
# router (same reasoning as videos.py's own top-level /videos/{id}/stream*
# routes) rather than bolted onto `router` above.
media_router = APIRouter(prefix="/heatmaps", tags=["heatmaps"])


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


def _snapshot_not_found() -> HTTPException:
    return HTTPException(
        status_code=404,
        detail=error_envelope("NOT_FOUND", "No heatmap snapshot of that type exists yet"),
    )


def _invalid_heatmap_type(raw_value: str) -> HTTPException:
    valid_values = ", ".join(t.value for t in HeatmapType)
    return HTTPException(
        status_code=400,
        detail=error_envelope(
            "INVALID_HEATMAP_TYPE",
            f"'{raw_value}' is not a valid heatmap type. Must be one of: {valid_values}",
        ),
    )


@router.get("/{session_id}/heatmaps")
def list_heatmaps(
    session_id: UUID,
    heatmap_type: HeatmapType | None = Query(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    query = db.query(HeatmapSnapshot).filter(HeatmapSnapshot.session_id == session_id)
    if heatmap_type is not None:
        query = query.filter(HeatmapSnapshot.heatmap_type == heatmap_type)

    rows = query.order_by(HeatmapSnapshot.created_at.desc()).all()
    items = [HeatmapSnapshotRead.model_validate(row) for row in rows]
    response = HeatmapSnapshotListResponse(items=items)
    return success_envelope(response.model_dump(mode="json"))


@router.get("/{session_id}/heatmaps/{heatmap_type}")
def get_latest_heatmap(
    session_id: UUID,
    heatmap_type: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    try:
        parsed_type = HeatmapType(heatmap_type)
    except ValueError:
        raise _invalid_heatmap_type(heatmap_type)

    row = (
        db.query(HeatmapSnapshot)
        .filter(
            HeatmapSnapshot.session_id == session_id,
            HeatmapSnapshot.heatmap_type == parsed_type,
        )
        .order_by(HeatmapSnapshot.created_at.desc())
        .first()
    )
    if row is None:
        raise _snapshot_not_found()

    return success_envelope(HeatmapSnapshotRead.model_validate(row).model_dump(mode="json"))


def _heatmap_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Heatmap snapshot not found")
    )


@media_router.get("/{heatmap_id}/access-token")
def get_heatmap_access_token(
    heatmap_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    # Resolution 1, Step 1: protected by NORMAL Bearer auth exactly like
    # every other route — mirrors GET /videos/{id}/stream-token's own shape
    # (Phase 21, Gap 2) so this obtains the short-lived token; the actual
    # image-byte route below is the one an <img> tag hits directly and
    # cannot attach a Bearer header to.
    snapshot = db.get(HeatmapSnapshot, heatmap_id)
    if snapshot is None:
        raise _heatmap_not_found()

    token = generate_heatmap_access_token(heatmap_id, current_user.id)
    return success_envelope(
        {
            "token": token,
            "expires_in_seconds": settings.HEATMAP_TOKEN_EXPIRE_MINUTES * 60,
        }
    )


@media_router.get("/{heatmap_id}/image")
def get_heatmap_image(heatmap_id: UUID, token: str | None = None, db: Session = Depends(get_db)):
    # Deliberately NOT protected by get_current_user — a browser <img> tag
    # issues a plain GET and cannot attach a custom Authorization header.
    # Validated instead via the REQUIRED ?token= query parameter,
    # independently of the main JWT flow. Same deliberate, narrow exception
    # to the JSON-envelope convention Phase 21's video /stream route
    # already established, for the identical reason: an <img> tag cannot
    # parse or display a JSON error body regardless of its shape.
    if token is None:
        raise HTTPException(status_code=401)
    try:
        validate_heatmap_access_token(token, heatmap_id)
    except HeatmapAccessTokenError:
        raise HTTPException(status_code=401)

    snapshot = db.get(HeatmapSnapshot, heatmap_id)
    if snapshot is None:
        raise HTTPException(status_code=404)

    # file_path (the real filesystem path component) is used ONLY
    # server-side here to locate the file — never included in any response
    # body, matching HeatmapSnapshotRead's own non-leaking discipline.
    file_path = get_storage_dir() / snapshot.file_path
    if not file_path.exists():
        raise HTTPException(status_code=404)

    # No Range-request support: a static JPEG isn't seekable content the
    # way video is — Starlette's FileResponse still works fine here, it
    # simply serves the full file (per Step 2's explicit "do not
    # over-engineer this" instruction).
    return FileResponse(path=file_path, media_type=JPEG_CONTENT_TYPE)
