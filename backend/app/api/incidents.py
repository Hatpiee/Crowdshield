from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.core.database import get_db
from app.core.deps import get_current_user, require_role
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.incident import Incident
from app.models.user import Role, User
from app.schemas.incident import (
    ActionRequest,
    IncidentEvidenceRead,
    IncidentRead,
    OperatorActionRead,
    SessionIncidentsRead,
)
from app.services import incident_service
from app.services.incident_service import IncidentDetail, InvalidIncidentTransitionError

router = APIRouter(tags=["incidents"])


def _session_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Session not found")
    )


def _incident_not_found() -> HTTPException:
    return HTTPException(
        status_code=404, detail=error_envelope("NOT_FOUND", "Incident not found")
    )


def _invalid_transition(exc: InvalidIncidentTransitionError) -> HTTPException:
    return HTTPException(status_code=409, detail=error_envelope("INVALID_TRANSITION", str(exc)))


def _detail_to_read(detail: IncidentDetail) -> IncidentRead:
    incident = detail.incident
    return IncidentRead(
        id=incident.id,
        session_id=incident.session_id,
        lifecycle_status=incident.lifecycle_status,
        closure_reason=incident.closure_reason,
        priority=incident.priority,
        acknowledged=incident.acknowledged,
        acknowledged_at=incident.acknowledged_at,
        acknowledged_by=incident.acknowledged_by,
        latest_recommendation=detail.latest_recommendation,
        linked_evidence=[IncidentEvidenceRead.model_validate(link) for link in detail.linked_evidence],
        operator_actions=[OperatorActionRead.model_validate(a) for a in detail.operator_actions],
        created_at=incident.created_at,
        updated_at=incident.updated_at,
    )


def _get_incident_or_404(db: Session, incident_id: UUID) -> Incident:
    incident = db.get(Incident, incident_id)
    if incident is None:
        raise _incident_not_found()
    return incident


@router.get("/sessions/{session_id}/incidents")
def list_session_incidents(
    session_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    if db.get(AnalysisSession, session_id) is None:
        raise _session_not_found()

    incidents = incident_service.get_session_incidents(db, session_id)
    items = [_detail_to_read(incident_service.get_incident_detail(db, i.id)) for i in incidents]
    return success_envelope(SessionIncidentsRead(items=items).model_dump(mode="json"))


@router.get("/incidents/{incident_id}")
def get_incident(
    incident_id: UUID,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    detail = incident_service.get_incident_detail(db, incident_id)
    if detail is None:
        raise _incident_not_found()
    return success_envelope(_detail_to_read(detail).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/acknowledge")
def acknowledge_incident(
    incident_id: UUID,
    body: ActionRequest = ActionRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    incident = _get_incident_or_404(db, incident_id)
    try:
        incident_service.acknowledge_incident(db, incident, current_user.id, body.notes)
    except InvalidIncidentTransitionError as exc:
        raise _invalid_transition(exc)
    detail = incident_service.get_incident_detail(db, incident_id)
    return success_envelope(_detail_to_read(detail).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/dismiss")
def dismiss_incident(
    incident_id: UUID,
    body: ActionRequest = ActionRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    incident = _get_incident_or_404(db, incident_id)
    try:
        incident_service.dismiss_incident(db, incident, current_user.id, body.notes)
    except InvalidIncidentTransitionError as exc:
        raise _invalid_transition(exc)
    detail = incident_service.get_incident_detail(db, incident_id)
    return success_envelope(_detail_to_read(detail).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/resolve")
def resolve_incident(
    incident_id: UUID,
    body: ActionRequest = ActionRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    incident = _get_incident_or_404(db, incident_id)
    try:
        incident_service.resolve_incident(db, incident, current_user.id, body.notes)
    except InvalidIncidentTransitionError as exc:
        raise _invalid_transition(exc)
    detail = incident_service.get_incident_detail(db, incident_id)
    return success_envelope(_detail_to_read(detail).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/false-positive")
def mark_false_positive(
    incident_id: UUID,
    body: ActionRequest = ActionRequest(),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    incident = _get_incident_or_404(db, incident_id)
    try:
        incident_service.mark_false_positive(db, incident, current_user.id, body.notes)
    except InvalidIncidentTransitionError as exc:
        raise _invalid_transition(exc)
    detail = incident_service.get_incident_detail(db, incident_id)
    return success_envelope(_detail_to_read(detail).model_dump(mode="json"))


@router.post("/incidents/{incident_id}/escalate")
def escalate_incident(
    incident_id: UUID,
    body: ActionRequest = ActionRequest(),
    # Resolution 3: the FIRST real use of require_role in this codebase
    # since it was built in Phase 2 — every route since has used
    # get_current_user for any authenticated user. §25's OPERATOR
    # permission list omits escalate; only ADMIN may perform it.
    current_user: User = Depends(require_role(Role.ADMIN)),
    db: Session = Depends(get_db),
):
    incident = _get_incident_or_404(db, incident_id)
    try:
        incident_service.escalate_incident(db, incident, current_user.id, body.notes)
    except InvalidIncidentTransitionError as exc:
        raise _invalid_transition(exc)
    detail = incident_service.get_incident_detail(db, incident_id)
    return success_envelope(_detail_to_read(detail).model_dump(mode="json"))
