"""Phase 16, Step 6: API tests for GET /sessions/{id}/evidence and
GET /evidence/{id} (Resolution 3's reasoned extension)."""

import uuid

import numpy as np

from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.frame import Frame
from app.services import evidence_service, session_service

from tests.test_evidence_builder import (
    _crowd_metrics,
    _observation,
    _risk_state_result,
    _trigger_decision,
    _vision_result,
)

FRAME_WIDTH = 64
FRAME_HEIGHT = 64


def _frame() -> Frame:
    image = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), 128, dtype=np.uint8)
    return Frame(frame_number=0, timestamp_seconds=0.0, image=image, width=FRAME_WIDTH, height=FRAME_HEIGHT)


def _persist_package(db_session, session):
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([_observation()]),
        vlm_call_succeeded=True,
    )
    return evidence_service.persist_evidence_package(db_session, result)


def test_list_session_evidence_requires_auth(client, db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    response = client.get(f"/api/v1/sessions/{session.id}/evidence")
    assert response.status_code == 401


def test_get_evidence_package_requires_auth(client):
    response = client.get(f"/api/v1/evidence/{uuid.uuid4()}")
    assert response.status_code == 401


def test_list_session_evidence_returns_persisted_packages(client, db_session, make_video, test_user, auth_headers):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)

    response = client.get(f"/api/v1/sessions/{session.id}/evidence", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    items = body["data"]["items"]
    assert len(items) == 1
    item = items[0]
    assert item["id"] == str(package.id)
    assert item["complete"] is True
    assert len(item["evidence_items"]) == 1
    # No raw filesystem paths leaked into the API response.
    assert "representative_frame_path" not in item
    assert "roi_crop_path" not in item


def test_get_evidence_package_returns_nested_items(client, db_session, make_video, test_user, auth_headers):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)

    response = client.get(f"/api/v1/evidence/{package.id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["id"] == str(package.id)
    assert len(body["data"]["evidence_items"]) == 1
    assert "region" in body["data"]["evidence_items"][0]


def test_get_evidence_package_not_found(client, auth_headers):
    response = client.get(f"/api/v1/evidence/{uuid.uuid4()}", headers=auth_headers)
    assert response.status_code == 404


def test_list_session_evidence_session_not_found(client, auth_headers):
    response = client.get(f"/api/v1/sessions/{uuid.uuid4()}/evidence", headers=auth_headers)
    assert response.status_code == 404
