"""Phase 19, Step 4: incident correlation tests."""

import uuid

from app.core.config import settings
from app.models.incident import Incident, IncidentEvidence, IncidentLifecycleStatus
from app.pipeline.decision_result import DecisionOutcome, DecisionResult, RecommendationType
from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.trigger_engine import TriggerDecision, TriggerType
from app.services import decision_service, evidence_service, incident_service, session_service

from tests.test_evidence_builder import _crowd_metrics, _frame, _observation, _risk_state_result, _vision_result


def _persist_pair(db_session, session, timestamp_seconds: float, outcome=DecisionOutcome.INCIDENT):
    trigger_decision = TriggerDecision(
        frame_number=int(timestamp_seconds * 30), timestamp_seconds=timestamp_seconds,
        trigger_type=TriggerType.RISK, reason="test trigger",
    )
    evidence_result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=trigger_decision,
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([_observation()]),
        vlm_call_succeeded=True,
    )
    package = evidence_service.persist_evidence_package(db_session, evidence_result)

    if outcome in (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH):
        decision = DecisionResult(
            decision_id=uuid.uuid4(), evidence_package_id=package.id,
            evidence_cited=["risk_score"], outcome=outcome,
            reasoning_summary="test", recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
            recommendation_rationale="test", projection_narrative=None,
            abstention_reason=None, confidence=evidence_result.confidence,
            binding_constraint=evidence_result.binding_constraint,
        )
    else:
        decision = DecisionResult(
            decision_id=uuid.uuid4(), evidence_package_id=package.id,
            evidence_cited=["risk_score"], outcome=outcome,
            reasoning_summary="test", recommendation=None,
            recommendation_rationale=None, projection_narrative=None,
            abstention_reason="test" if outcome == DecisionOutcome.ABSTAIN else None,
            confidence=evidence_result.confidence, binding_constraint=evidence_result.binding_constraint,
        )
    decision_row = decision_service.persist_decision_result(db_session, decision)
    return package, decision_row


def test_first_incident_worthy_decision_creates_new_incident(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package, decision_row = _persist_pair(db_session, session, timestamp_seconds=0.0)
    assert incident_service.is_decision_incident_worthy(db_session, decision_row) is True

    incident = incident_service.correlate_or_create_incident(db_session, session.id, decision_row, package)

    assert incident.lifecycle_status == IncidentLifecycleStatus.DETECTED
    links = db_session.query(IncidentEvidence).filter(IncidentEvidence.incident_id == incident.id).all()
    assert len(links) == 1
    all_incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(all_incidents) == 1


def test_second_decision_within_window_correlates_and_transitions_to_active(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package1, decision1 = _persist_pair(db_session, session, timestamp_seconds=0.0)
    incident1 = incident_service.correlate_or_create_incident(db_session, session.id, decision1, package1)
    assert incident1.lifecycle_status == IncidentLifecycleStatus.DETECTED

    package2, decision2 = _persist_pair(db_session, session, timestamp_seconds=10.0)
    incident2 = incident_service.correlate_or_create_incident(db_session, session.id, decision2, package2)

    assert incident2.id == incident1.id
    assert incident2.lifecycle_status == IncidentLifecycleStatus.ACTIVE
    all_incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(all_incidents) == 1
    links = db_session.query(IncidentEvidence).filter(IncidentEvidence.incident_id == incident1.id).all()
    assert len(links) == 2


def test_third_decision_active_incident_self_loops(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package1, decision1 = _persist_pair(db_session, session, timestamp_seconds=0.0)
    incident_service.correlate_or_create_incident(db_session, session.id, decision1, package1)
    package2, decision2 = _persist_pair(db_session, session, timestamp_seconds=10.0)
    incident_service.correlate_or_create_incident(db_session, session.id, decision2, package2)

    package3, decision3 = _persist_pair(db_session, session, timestamp_seconds=20.0)
    incident3 = incident_service.correlate_or_create_incident(db_session, session.id, decision3, package3)

    assert incident3.lifecycle_status == IncidentLifecycleStatus.ACTIVE
    all_incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(all_incidents) == 1
    links = db_session.query(IncidentEvidence).filter(IncidentEvidence.incident_id == incident3.id).all()
    assert len(links) == 3


def test_decision_after_window_elapsed_creates_new_incident(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package1, decision1 = _persist_pair(db_session, session, timestamp_seconds=0.0)
    incident1 = incident_service.correlate_or_create_incident(db_session, session.id, decision1, package1)

    far_timestamp = settings.INCIDENT_CORRELATION_WINDOW_SECONDS + 100.0
    package2, decision2 = _persist_pair(db_session, session, timestamp_seconds=far_timestamp)
    incident2 = incident_service.correlate_or_create_incident(db_session, session.id, decision2, package2)

    assert incident2.id != incident1.id
    all_incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(all_incidents) == 2


def test_decision_when_only_incident_is_terminal_creates_new_incident(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package1, decision1 = _persist_pair(db_session, session, timestamp_seconds=0.0)
    incident1 = incident_service.correlate_or_create_incident(db_session, session.id, decision1, package1)
    incident_service.resolve_incident(db_session, incident1, user.id)

    # Well within what would otherwise be the correlation window.
    package2, decision2 = _persist_pair(db_session, session, timestamp_seconds=5.0)
    incident2 = incident_service.correlate_or_create_incident(db_session, session.id, decision2, package2)

    assert incident2.id != incident1.id
    assert incident2.lifecycle_status == IncidentLifecycleStatus.DETECTED
    all_incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(all_incidents) == 2


def test_superseded_incident_decision_is_not_incident_worthy(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package, original_decision = _persist_pair(db_session, session, timestamp_seconds=0.0, outcome=DecisionOutcome.INCIDENT)
    assert incident_service.is_decision_incident_worthy(db_session, original_decision) is True

    # Simulate Phase 18's superseding mechanism: a new ABSTAIN decision
    # whose superseded_decision_id points at the original.
    superseding = DecisionResult(
        decision_id=uuid.uuid4(), evidence_package_id=package.id,
        evidence_cited=[], outcome=DecisionOutcome.ABSTAIN,
        reasoning_summary="superseded", recommendation=None, recommendation_rationale=None,
        projection_narrative=None, abstention_reason="verification_failed",
        confidence=original_decision.confidence, binding_constraint=original_decision.binding_constraint,
        superseded_decision_id=original_decision.id,
    )
    decision_service.persist_decision_result(db_session, superseding)

    db_session.expire_all()
    reloaded_original = decision_service.get_decision_result(db_session, original_decision.id)
    assert incident_service.is_decision_incident_worthy(db_session, reloaded_original) is False

    all_incidents = db_session.query(Incident).filter(Incident.session_id == session.id).all()
    assert len(all_incidents) == 0
