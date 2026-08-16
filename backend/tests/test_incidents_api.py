"""Phase 19, Step 4: API tests for the incident routes."""

import uuid

from app.models.user import Role
from app.services import incident_service, session_service

from tests.test_incident_correlation import _persist_pair


def _login_headers(client, email, password):
    response = client.post("/api/v1/auth/verify-credentials", json={"email": email, "password": password})
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}


def _setup_incident(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package, decision = _persist_pair(db_session, session, timestamp_seconds=0.0)
    incident = incident_service.correlate_or_create_incident(db_session, session.id, decision, package)
    return session, incident


ACTION_ROUTES = ["acknowledge", "dismiss", "resolve", "false-positive", "escalate"]


def test_all_routes_require_auth(client, db_session, make_video, test_user):
    session, incident = _setup_incident(db_session, make_video, test_user)

    assert client.get(f"/api/v1/sessions/{session.id}/incidents").status_code == 401
    assert client.get(f"/api/v1/incidents/{incident.id}").status_code == 401
    for route in ACTION_ROUTES:
        assert client.post(f"/api/v1/incidents/{incident.id}/{route}").status_code == 401


def test_list_session_incidents_happy_path(client, db_session, make_video, test_user, auth_headers):
    session, incident = _setup_incident(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/incidents", headers=auth_headers)
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["id"] == str(incident.id)
    assert items[0]["lifecycle_status"] == "DETECTED"
    assert len(items[0]["linked_evidence"]) == 1


def test_get_incident_happy_path(client, db_session, make_video, test_user, auth_headers):
    _, incident = _setup_incident(db_session, make_video, test_user)

    response = client.get(f"/api/v1/incidents/{incident.id}", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["id"] == str(incident.id)


def test_get_incident_timeline_nests_full_evidence_and_decision(
    client, db_session, make_video, test_user, auth_headers
):
    # Phase 23, Resolution 1: linked_evidence entries now nest the REAL,
    # full EvidencePackageRead + DecisionResultRead (not just ids), ordered
    # chronologically by correlated_at.
    session, incident = _setup_incident(db_session, make_video, test_user)

    response = client.get(f"/api/v1/incidents/{incident.id}", headers=auth_headers)
    assert response.status_code == 200
    timeline = response.json()["data"]["linked_evidence"]
    assert len(timeline) == 1

    entry = timeline[0]
    assert set(entry.keys()) == {"evidence_package", "decision_result", "correlated_at"}

    package = entry["evidence_package"]
    assert package["session_id"] == str(session.id)
    assert "trigger_reason" in package
    assert "crowd_metrics_summary" in package
    assert len(package["evidence_items"]) == 1
    # No raw filesystem paths leaked anywhere in this response.
    assert "representative_frame_path" not in package
    assert "roi_crop_path" not in package
    body_text = response.text
    assert "representative_frame_path" not in body_text
    assert "roi_crop_path" not in body_text
    assert "hashed_password" not in body_text

    decision = entry["decision_result"]
    assert decision["evidence_package_id"] == package["id"]
    assert "reasoning_summary" in decision
    assert "binding_constraint" in decision


def test_incidents_list_widget_shape_unaffected_by_timeline_nesting(
    client, db_session, make_video, test_user, auth_headers
):
    # Confirms Phase 22's incidents-list widget (which reads only
    # lifecycle_status/priority/acknowledged/latest_recommendation/
    # created_at/updated_at/closure_reason/id — never linked_evidence) still
    # renders correctly: those exact fields are still present and correctly
    # shaped after Resolution 1's linked_evidence schema change.
    session, incident = _setup_incident(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/incidents", headers=auth_headers)
    assert response.status_code == 200
    item = response.json()["data"]["items"][0]
    for field in (
        "id", "lifecycle_status", "closure_reason", "priority", "acknowledged",
        "latest_recommendation", "created_at", "updated_at",
    ):
        assert field in item


def test_get_incident_not_found(client, auth_headers):
    response = client.get(f"/api/v1/incidents/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


def test_list_session_incidents_session_not_found(client, auth_headers):
    response = client.get(f"/api/v1/sessions/{uuid.uuid4()}/incidents", headers=auth_headers)
    assert response.status_code == 404


def test_acknowledge_happy_path(client, db_session, make_video, test_user, auth_headers):
    _, incident = _setup_incident(db_session, make_video, test_user)

    response = client.post(f"/api/v1/incidents/{incident.id}/acknowledge", headers=auth_headers, json={"notes": "on it"})
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["acknowledged"] is True
    assert len(data["operator_actions"]) == 1
    assert data["operator_actions"][0]["action_type"] == "ACKNOWLEDGE"
    assert data["operator_actions"][0]["notes"] == "on it"


def test_dismiss_happy_path(client, db_session, make_video, test_user, auth_headers):
    _, incident = _setup_incident(db_session, make_video, test_user)

    response = client.post(f"/api/v1/incidents/{incident.id}/dismiss", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["lifecycle_status"] == "RESOLVED"
    assert data["closure_reason"] == "DISMISSED"


def test_resolve_happy_path(client, db_session, make_video, test_user, auth_headers):
    _, incident = _setup_incident(db_session, make_video, test_user)

    response = client.post(f"/api/v1/incidents/{incident.id}/resolve", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["lifecycle_status"] == "RESOLVED"
    assert data["closure_reason"] == "RESOLVED"


def test_false_positive_happy_path(client, db_session, make_video, test_user, auth_headers):
    _, incident = _setup_incident(db_session, make_video, test_user)

    response = client.post(f"/api/v1/incidents/{incident.id}/false-positive", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["lifecycle_status"] == "FALSE_POSITIVE"


def test_action_on_nonexistent_incident_404(client, auth_headers):
    response = client.post(f"/api/v1/incidents/{uuid.uuid4()}/resolve", headers=auth_headers)
    assert response.status_code == 404


def test_invalid_transition_returns_409(client, db_session, make_video, test_user, auth_headers):
    _, incident = _setup_incident(db_session, make_video, test_user)
    first = client.post(f"/api/v1/incidents/{incident.id}/resolve", headers=auth_headers)
    assert first.status_code == 200

    second = client.post(f"/api/v1/incidents/{incident.id}/resolve", headers=auth_headers)
    assert second.status_code == 409


def test_escalate_requires_admin_operator_gets_403(client, db_session, make_video, test_user, auth_headers):
    # test_user (auth_headers) is OPERATOR-role, per conftest.py's default.
    _, incident = _setup_incident(db_session, make_video, test_user)

    response = client.post(f"/api/v1/incidents/{incident.id}/escalate", headers=auth_headers)
    assert response.status_code == 403


def test_escalate_succeeds_for_admin(client, db_session, make_video, make_user, test_user):
    _, incident = _setup_incident(db_session, make_video, test_user)
    admin_password = "adminpass123"
    make_user("real_admin@example.com", admin_password, role=Role.ADMIN)
    admin_headers = _login_headers(client, "real_admin@example.com", admin_password)

    response = client.post(f"/api/v1/incidents/{incident.id}/escalate", headers=admin_headers)
    assert response.status_code == 200
    assert response.json()["data"]["priority"] == "ELEVATED"
