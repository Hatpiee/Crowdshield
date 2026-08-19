"""Phase 17, Step 7: API tests for GET /sessions/{id}/decisions and
GET /decisions/{id}, mirroring Phase 16's test_evidence_api.py patterns."""

import uuid

from app.pipeline.decision_result import (
    DecisionOutcome,
    DecisionResult,
    EventClassification,
    RecommendationType,
)
from app.services import decision_service, evidence_service, session_service

from tests.test_evidence_builder import (
    _crowd_metrics,
    _frame,
    _observation,
    _risk_state_result,
    _trigger_decision,
    _vision_result,
)
from app.pipeline.evidence_builder import EvidenceBuilder


def _persisted_decision(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    evidence_result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([_observation()]),
        vlm_call_succeeded=True,
    )
    package = evidence_service.persist_evidence_package(db_session, evidence_result)
    decision = DecisionResult(
        decision_id=uuid.uuid4(), evidence_package_id=package.id,
        evidence_cited=["risk_score"], outcome=DecisionOutcome.WATCH,
        reasoning_summary="test reasoning", recommendation=RecommendationType.BROADCAST_PUBLIC_ANNOUNCEMENT,
        recommendation_rationale="test rationale", projection_narrative=None,
        event_classification=EventClassification.CROWD_CRUSH,
        abstention_reason=None, confidence=0.8, binding_constraint="density_estimation_confidence",
    )
    row = decision_service.persist_decision_result(db_session, decision)
    return session, row


def test_list_session_decisions_requires_auth(client, db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    response = client.get(f"/api/v1/sessions/{session.id}/decisions")
    assert response.status_code == 401


def test_get_decision_requires_auth(client):
    response = client.get(f"/api/v1/decisions/{uuid.uuid4()}")
    assert response.status_code == 401


def test_list_session_decisions_returns_persisted_rows(client, db_session, make_video, test_user, auth_headers):
    session, row = _persisted_decision(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/decisions", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(row.id)
    assert items[0]["outcome"] == "WATCH"
    assert items[0]["recommendation"] == "BROADCAST_PUBLIC_ANNOUNCEMENT"


def test_get_decision_returns_full_record(client, db_session, make_video, test_user, auth_headers):
    _, row = _persisted_decision(db_session, make_video, test_user)

    response = client.get(f"/api/v1/decisions/{row.id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(row.id)


def test_get_decision_not_found(client, auth_headers):
    response = client.get(f"/api/v1/decisions/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


def test_list_session_decisions_session_not_found(client, auth_headers):
    response = client.get(f"/api/v1/sessions/{uuid.uuid4()}/decisions", headers=auth_headers)
    assert response.status_code == 404
