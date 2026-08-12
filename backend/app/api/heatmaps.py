from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.heatmap import HeatmapSnapshot, HeatmapType
from app.models.user import User
from app.schemas.heatmap import HeatmapSnapshotListResponse, HeatmapSnapshotRead

# Mounted under the SAME "/sessions" prefix as app/api/sessions.py (Phase
# 4) — a separate router (rather than adding routes directly to
# sessions.py) keeps this phase's genuinely new, persisted-resource
# surface (Resolution 3) cleanly separated in its own module, while still
# nesting under /sessions/{session_id}/... per §26's frozen route shape.
#
# READ-ONLY / METADATA-ONLY (Resolution 3): these routes never serve raw
# image bytes (deferred to the dashboard integration phase, same as video
# streaming's deferral in Phase 3) and never trigger generation on demand
# — they only query already-persisted HeatmapSnapshot rows.
router = APIRouter(prefix="/sessions", tags=["heatmaps"])


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
