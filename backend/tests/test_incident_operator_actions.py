"""Phase 19, Step 4: operator-action tests."""

import pytest

from app.models.incident import (
    ClosureReason,
    Incident,
    IncidentLifecycleStatus,
    IncidentPriority,
    OperatorAction,
    OperatorActionType,
)
from app.services import incident_service
from app.services.incident_service import InvalidIncidentTransitionError

from tests.test_incident_correlation import _persist_pair


def _new_incident(db_session, session):
    package, decision = _persist_pair(db_session, session, timestamp_seconds=0.0)
    return incident_service.correlate_or_create_incident(db_session, session.id, decision, package)


def _actions_for(db_session, incident_id):
    return db_session.query(OperatorAction).filter(OperatorAction.incident_id == incident_id).all()


def test_acknowledge_sets_fields_and_records_action(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)

    result = incident_service.acknowledge_incident(db_session, incident, user.id, notes="looking into it")

    assert result.acknowledged is True
    assert result.acknowledged_at is not None
    assert result.acknowledged_by == user.id
    actions = _actions_for(db_session, incident.id)
    assert len(actions) == 1
    assert actions[0].action_type == OperatorActionType.ACKNOWLEDGE
    assert actions[0].performed_by == user.id
    assert actions[0].notes == "looking into it"


def test_re_acknowledge_is_idempotent_but_still_audited(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)

    incident_service.acknowledge_incident(db_session, incident, user.id)
    result = incident_service.acknowledge_incident(db_session, incident, user.id)

    assert result.acknowledged is True
    # Chosen idempotency behavior: re-acknowledgment does NOT raise — it's
    # an orthogonal flag (Resolution 2), not a lifecycle transition — but
    # EVERY invocation still gets its own audit row (§20: never applied
    # silently), so two calls produce two OperatorAction rows.
    actions = _actions_for(db_session, incident.id)
    assert len(actions) == 2


def test_dismiss_and_resolve_set_different_closure_reasons(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    incident_a = _new_incident(db_session, session)
    dismissed = incident_service.dismiss_incident(db_session, incident_a, user.id)
    assert dismissed.lifecycle_status == IncidentLifecycleStatus.RESOLVED
    assert dismissed.closure_reason == ClosureReason.DISMISSED

    incident_b = _new_incident(db_session, session)
    resolved = incident_service.resolve_incident(db_session, incident_b, user.id)
    assert resolved.lifecycle_status == IncidentLifecycleStatus.RESOLVED
    assert resolved.closure_reason == ClosureReason.RESOLVED


def test_mark_false_positive(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)

    result = incident_service.mark_false_positive(db_session, incident, user.id)
    assert result.lifecycle_status == IncidentLifecycleStatus.FALSE_POSITIVE
    assert result.closure_reason is None
    actions = _actions_for(db_session, incident.id)
    assert actions[0].action_type == OperatorActionType.MARK_FALSE_POSITIVE


def test_escalate_sets_elevated_priority(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)

    result = incident_service.escalate_incident(db_session, incident, user.id)
    assert result.priority == IncidentPriority.ELEVATED
    actions = _actions_for(db_session, incident.id)
    assert actions[0].action_type == OperatorActionType.ESCALATE


def test_resolve_already_false_positive_raises_409_equivalent(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)
    incident_service.mark_false_positive(db_session, incident, user.id)

    with pytest.raises(InvalidIncidentTransitionError):
        incident_service.resolve_incident(db_session, incident, user.id)


def test_dismiss_already_resolved_raises(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)
    incident_service.resolve_incident(db_session, incident, user.id)

    with pytest.raises(InvalidIncidentTransitionError):
        incident_service.dismiss_incident(db_session, incident, user.id)


def test_acknowledge_already_resolved_raises(db_session, make_video, test_user):
    from app.services import session_service
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)
    incident_service.resolve_incident(db_session, incident, user.id)

    with pytest.raises(InvalidIncidentTransitionError):
        incident_service.acknowledge_incident(db_session, incident, user.id)


def test_performed_by_recorded_as_real_authenticated_user(db_session, make_video, make_user, test_user):
    from app.services import session_service
    user, _ = test_user
    other_user = make_user("second_operator@example.com", "pw123456")
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    incident = _new_incident(db_session, session)

    incident_service.escalate_incident(db_session, incident, other_user.id)

    actions = _actions_for(db_session, incident.id)
    assert actions[0].performed_by == other_user.id
    assert actions[0].performed_by != user.id
