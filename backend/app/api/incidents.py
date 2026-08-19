from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.api.decisions import _decision_to_read
from app.api.evidence import _to_read as _evidence_package_to_read
from app.core.database import get_db
from app.core.deps import get_current_user, require_role
from app.core.response import error_envelope, success_envelope
from app.models.analysis_session import AnalysisSession
from app.models.incident import Incident
from app.models.user import Role, User
from app.schemas.incident import (
    ActionRequest,
    IncidentRead,
    IncidentTimelineEntryRead,
    OperatorActionRead,
    SessionIncidentsRead,
)
from app.services import decision_service, evidence_service, incident_service
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


def _operator_actions_to_read(db: Session, actions: list) -> list[OperatorActionRead]:
    # One small, batched lookup rather than a per-action query — actions
    # is typically a handful of rows per incident.
    user_ids = {a.performed_by for a in actions}
    emails = {}
    if user_ids:
        rows = db.query(User.id, User.email).filter(User.id.in_(user_ids)).all()
        emails = {uid: email for uid, email in rows}
    return [
        OperatorActionRead(
            id=a.id,
            action_type=a.action_type,
            performed_by=a.performed_by,
            performed_by_email=emails.get(a.performed_by, "unknown"),
            notes=a.notes,
            created_at=a.created_at,
        )
        for a in actions
    ]


def _timeline_entry_to_read(db: Session, link) -> IncidentTimelineEntryRead:
    # Resolution 1: reuses the SAME conversion helpers evidence.py/
    # decisions.py already use for their own single-resource GET routes —
    # no duplicated field-mapping logic for EvidencePackageRead/
    # DecisionResultRead.
    evidence_entry = evidence_service.get_evidence_package(db, link.evidence_package_id)
    decision_row = decision_service.get_decision_result(db, link.decision_result_id)
    verification_row = decision_service.get_verification_for_decision(db, link.decision_result_id)
    return IncidentTimelineEntryRead(
        evidence_package=_evidence_package_to_read(evidence_entry),
        decision_result=_decision_to_read(decision_row, verification_row),
        correlated_at=link.correlated_at,
    )


def _detail_to_read(db: Session, detail: IncidentDetail) -> IncidentRead:
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
        latest_event_classification=detail.latest_event_classification,
        # Already chronologically ordered (correlated_at.asc()) by
        # get_incident_detail (Phase 19, unchanged).
        linked_evidence=[_timeline_entry_to_read(db, link) for link in detail.linked_evidence],
        operator_actions=_operator_actions_to_read(db, detail.operator_actions),
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
    items = [_detail_to_read(db, incident_service.get_incident_detail(db, i.id)) for i in incidents]
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
    return success_envelope(_detail_to_read(db, detail).model_dump(mode="json"))


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
    return success_envelope(_detail_to_read(db, detail).model_dump(mode="json"))


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
    return success_envelope(_detail_to_read(db, detail).model_dump(mode="json"))


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
    return success_envelope(_detail_to_read(db, detail).model_dump(mode="json"))


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
    return success_envelope(_detail_to_read(db, detail).model_dump(mode="json"))


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
    return success_envelope(_detail_to_read(db, detail).model_dump(mode="json"))
