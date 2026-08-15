"""Incident correlation + lifecycle + operator-action persistence — Phase
19, §19/§20/§21. Mirrors the established I/O-around-pure-logic pattern:
this is the ONLY module that writes `incidents`/`incident_evidence`/
`operator_actions` rows.

CORRELATION PRECONDITION: `correlate_or_create_incident` must ONLY be
called for decisions where `is_decision_incident_worthy(db, decision)`
returned True — it does not re-check outcome/supersession itself, matching
the established should_verify()/Verifier.verify() separation-of-concerns
pattern (Phase 18, Decision D): the CALLER decides applicability.
"""

import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone

from sqlalchemy.orm import Session

from app.core.config import settings
from app.models.decision import DecisionResultRow
from app.models.evidence import EvidencePackage
from app.models.incident import (
    ClosureReason,
    Incident,
    IncidentEvidence,
    IncidentLifecycleStatus,
    IncidentPriority,
    OperatorAction,
    OperatorActionType,
)
from app.pipeline.decision_result import DecisionOutcome

# Only DETECTED/ACTIVE incidents are correlation-eligible (Frozen
# Decision) — RESOLVED/FALSE_POSITIVE are terminal.
_CORRELATABLE_STATUSES = (IncidentLifecycleStatus.DETECTED, IncidentLifecycleStatus.ACTIVE)


class InvalidIncidentTransitionError(Exception):
    """Raised when an operator action is attempted against an incident
    that is not in an eligible state for that action — routes map this to
    a 409, never silently applied (§20)."""


def is_decision_incident_worthy(db: Session, decision_result: DecisionResultRow) -> bool:
    """Decision #1: an outcome=INCIDENT decision is only genuinely
    incident-worthy if NO other DecisionResult's superseded_decision_id
    points at it — a decision that failed Phase 18 verification and was
    superseded by a fallback ABSTAIN must NEVER create or correlate into
    an incident."""
    if decision_result.outcome != DecisionOutcome.INCIDENT:
        return False

    superseded_by = (
        db.query(DecisionResultRow)
        .filter(DecisionResultRow.superseded_decision_id == decision_result.id)
        .first()
    )
    return superseded_by is None


def _most_recent_linked_timestamp(db: Session, incident_id: uuid.UUID) -> float | None:
    row = (
        db.query(EvidencePackage.timestamp_seconds)
        .join(IncidentEvidence, IncidentEvidence.evidence_package_id == EvidencePackage.id)
        .filter(IncidentEvidence.incident_id == incident_id)
        .order_by(EvidencePackage.timestamp_seconds.desc())
        .first()
    )
    return row[0] if row is not None else None


def correlate_or_create_incident(
    db: Session,
    session_id: uuid.UUID,
    decision_result: DecisionResultRow,
    evidence_package: EvidencePackage,
) -> Incident:
    candidates = (
        db.query(Incident)
        .filter(Incident.session_id == session_id, Incident.lifecycle_status.in_(_CORRELATABLE_STATUSES))
        .all()
    )

    matched_incident: Incident | None = None
    for candidate in candidates:
        last_timestamp = _most_recent_linked_timestamp(db, candidate.id)
        if last_timestamp is None:
            continue
        if abs(evidence_package.timestamp_seconds - last_timestamp) <= settings.INCIDENT_CORRELATION_WINDOW_SECONDS:
            matched_incident = candidate
            break

    was_correlated_into_existing = matched_incident is not None
    if matched_incident is None:
        matched_incident = Incident(session_id=session_id, lifecycle_status=IncidentLifecycleStatus.DETECTED)
        db.add(matched_incident)
        db.flush()

    db.add(
        IncidentEvidence(
            incident_id=matched_incident.id,
            evidence_package_id=evidence_package.id,
            decision_result_id=decision_result.id,
        )
    )

    # Diagram 9's exact labeled transition: DETECTED -> ACTIVE is
    # specifically triggered by a NEW correlated EvidencePackage arriving
    # while status is DETECTED — i.e. a SECOND evidence event correlating
    # into an already-existing incident, never the first evidence that
    # creates the incident in the first place (a brand-new incident stays
    # DETECTED after its own founding evidence link). ACTIVE stays ACTIVE
    # (self-loop) on further correlation.
    if was_correlated_into_existing and matched_incident.lifecycle_status == IncidentLifecycleStatus.DETECTED:
        matched_incident.lifecycle_status = IncidentLifecycleStatus.ACTIVE

    db.commit()
    db.refresh(matched_incident)
    return matched_incident


def _require_status(incident: Incident, allowed: tuple[IncidentLifecycleStatus, ...], action_name: str) -> None:
    if incident.lifecycle_status not in allowed:
        raise InvalidIncidentTransitionError(
            f"Cannot {action_name} incident {incident.id}: lifecycle_status="
            f"{incident.lifecycle_status.value} is not in {[s.value for s in allowed]}"
        )


def _record_action(
    db: Session, incident_id: uuid.UUID, action_type: OperatorActionType, performed_by: uuid.UUID, notes: str | None
) -> OperatorAction:
    action = OperatorAction(
        incident_id=incident_id, action_type=action_type, performed_by=performed_by, notes=notes,
    )
    db.add(action)
    return action


def acknowledge_incident(
    db: Session, incident: Incident, performed_by: uuid.UUID, notes: str | None = None
) -> Incident:
    # Resolution 2: acknowledged is an ORTHOGONAL flag, not a lifecycle
    # transition — deliberately idempotent/re-appliable (unlike the four
    # lifecycle-transition actions below), since re-acknowledging an
    # already-acknowledged incident is a normal, harmless operator action,
    # not an invalid state transition. Still allowed only on non-terminal
    # incidents (DETECTED/ACTIVE) — acknowledging a closed incident makes
    # no operational sense.
    _require_status(incident, _CORRELATABLE_STATUSES, "acknowledge")

    incident.acknowledged = True
    incident.acknowledged_at = datetime.now(timezone.utc)
    incident.acknowledged_by = performed_by
    _record_action(db, incident.id, OperatorActionType.ACKNOWLEDGE, performed_by, notes)

    db.commit()
    db.refresh(incident)
    return incident


def dismiss_incident(
    db: Session, incident: Incident, performed_by: uuid.UUID, notes: str | None = None
) -> Incident:
    _require_status(incident, _CORRELATABLE_STATUSES, "dismiss")

    incident.lifecycle_status = IncidentLifecycleStatus.RESOLVED
    incident.closure_reason = ClosureReason.DISMISSED
    _record_action(db, incident.id, OperatorActionType.DISMISS, performed_by, notes)

    db.commit()
    db.refresh(incident)
    return incident


def resolve_incident(
    db: Session, incident: Incident, performed_by: uuid.UUID, notes: str | None = None
) -> Incident:
    _require_status(incident, _CORRELATABLE_STATUSES, "resolve")

    incident.lifecycle_status = IncidentLifecycleStatus.RESOLVED
    incident.closure_reason = ClosureReason.RESOLVED
    _record_action(db, incident.id, OperatorActionType.RESOLVE, performed_by, notes)

    db.commit()
    db.refresh(incident)
    return incident


def mark_false_positive(
    db: Session, incident: Incident, performed_by: uuid.UUID, notes: str | None = None
) -> Incident:
    _require_status(incident, _CORRELATABLE_STATUSES, "mark_false_positive")

    incident.lifecycle_status = IncidentLifecycleStatus.FALSE_POSITIVE
    _record_action(db, incident.id, OperatorActionType.MARK_FALSE_POSITIVE, performed_by, notes)

    db.commit()
    db.refresh(incident)
    return incident


def escalate_incident(
    db: Session, incident: Incident, performed_by: uuid.UUID, notes: str | None = None
) -> Incident:
    # Resolution 3: state-only — sets priority=ELEVATED, fully audited, but
    # performs NO notification delivery of any kind (no such infrastructure
    # exists anywhere in this project's scope). ADMIN-only enforcement
    # lives at the route layer (require_role), not here.
    _require_status(incident, _CORRELATABLE_STATUSES, "escalate")

    incident.priority = IncidentPriority.ELEVATED
    _record_action(db, incident.id, OperatorActionType.ESCALATE, performed_by, notes)

    db.commit()
    db.refresh(incident)
    return incident


def get_session_incidents(db: Session, session_id: uuid.UUID) -> list[Incident]:
    return (
        db.query(Incident)
        .filter(Incident.session_id == session_id)
        .order_by(Incident.created_at.desc())
        .all()
    )


@dataclass
class IncidentDetail:
    incident: Incident
    linked_evidence: list[IncidentEvidence] = field(default_factory=list)
    operator_actions: list[OperatorAction] = field(default_factory=list)
    latest_recommendation: str | None = None


def get_incident_detail(db: Session, incident_id: uuid.UUID) -> IncidentDetail | None:
    incident = db.get(Incident, incident_id)
    if incident is None:
        return None

    linked_evidence = (
        db.query(IncidentEvidence)
        .filter(IncidentEvidence.incident_id == incident_id)
        .order_by(IncidentEvidence.correlated_at.asc())
        .all()
    )
    operator_actions = (
        db.query(OperatorAction)
        .filter(OperatorAction.incident_id == incident_id)
        .order_by(OperatorAction.created_at.desc())
        .all()
    )

    latest_recommendation = None
    if linked_evidence:
        latest_link = max(linked_evidence, key=lambda link: link.correlated_at)
        latest_decision = db.get(DecisionResultRow, latest_link.decision_result_id)
        if latest_decision is not None and latest_decision.recommendation is not None:
            latest_recommendation = latest_decision.recommendation.value

    return IncidentDetail(
        incident=incident,
        linked_evidence=linked_evidence,
        operator_actions=operator_actions,
        latest_recommendation=latest_recommendation,
    )
