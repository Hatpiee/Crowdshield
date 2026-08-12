"""Phase 13, Step 5: GET /sessions/{id}/risk."""

from app.models.risk_event import RiskEvent
from app.pipeline.risk_state import RiskState
from app.services import session_service


def _make_session_with_transitions(db_session, make_video, test_user):
    video = make_video()
    user, _ = test_user
    session = session_service.create_session(db_session, video.id, user.id)

    event1 = RiskEvent(
        session_id=session.id,
        previous_state=RiskState.NORMAL,
        new_state=RiskState.ELEVATED,
        frame_number=30,
        timestamp_seconds=1.0,
        risk_score_at_transition=45.0,
    )
    event2 = RiskEvent(
        session_id=session.id,
        previous_state=RiskState.ELEVATED,
        new_state=RiskState.CRITICAL,
        frame_number=90,
        timestamp_seconds=3.0,
        risk_score_at_transition=70.0,
    )
    db_session.add(event1)
    db_session.add(event2)
    db_session.commit()
    return session


def test_get_risk_without_auth_returns_401(client, db_session, make_video, test_user):
    session = _make_session_with_transitions(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/risk")
    assert response.status_code == 401


def test_get_risk_with_real_transitions_returns_correct_chronological_data(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_transitions(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/risk", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    data = body["data"]
    assert data["current_state"] == "CRITICAL"
    assert len(data["transition_history"]) == 2
    assert data["transition_history"][0]["new_state"] == "ELEVATED"
    assert data["transition_history"][1]["new_state"] == "CRITICAL"
    assert data["transition_history"][0]["frame_number"] == 30
    assert data["transition_history"][1]["frame_number"] == 90


def test_get_risk_with_zero_transitions_returns_200_not_404(
    client, auth_headers, db_session, make_video, test_user
):
    video = make_video()
    user, _ = test_user
    session = session_service.create_session(db_session, video.id, user.id)

    response = client.get(f"/api/v1/sessions/{session.id}/risk", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["current_state"] is None
    assert body["data"]["transition_history"] == []


def test_get_risk_nonexistent_session_returns_404(client, auth_headers):
    response = client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/risk",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"
