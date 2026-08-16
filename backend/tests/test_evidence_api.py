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


# Phase 23, Step 2/4: evidence frame/roi image-serving routes — access-token
# issuance + token-gated byte serving, real image bytes, and auth-failure
# cases. Mirrors test_video_streaming.py / test_heatmaps_api.py's own
# coverage shape for the analogous video/heatmap routes.


def _get_evidence_token(client, auth_headers, evidence_id) -> str:
    response = client.get(f"/api/v1/evidence/{evidence_id}/access-token", headers=auth_headers)
    assert response.status_code == 200
    return response.json()["data"]["token"]


def test_evidence_access_token_route_requires_auth(client, db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)

    response = client.get(f"/api/v1/evidence/{package.id}/access-token")
    assert response.status_code == 401


def test_evidence_access_token_route_returns_a_real_token(
    client, auth_headers, db_session, make_video, test_user
):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)

    response = client.get(f"/api/v1/evidence/{package.id}/access-token", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"]["token"], str) and body["data"]["token"]
    assert body["data"]["expires_in_seconds"] > 0


def test_evidence_access_token_route_nonexistent_package_returns_404(client, auth_headers):
    response = client.get(f"/api/v1/evidence/{uuid.uuid4()}/access-token", headers=auth_headers)
    assert response.status_code == 404


def test_evidence_frame_image_served_with_valid_token_returns_real_jpeg_bytes(
    client, auth_headers, db_session, make_video, test_user
):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)
    token = _get_evidence_token(client, auth_headers, package.id)

    real_bytes = (
        EvidenceBuilder().get_storage_dir() / f"{session.id}/{package.id}_frame.jpg"
    ).read_bytes()

    response = client.get(f"/api/v1/evidence/{package.id}/frame-image?token={token}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == real_bytes


def test_evidence_roi_image_served_with_valid_token_returns_real_jpeg_bytes(
    client, auth_headers, db_session, make_video, test_user
):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)
    token = _get_evidence_token(client, auth_headers, package.id)

    real_bytes = (
        EvidenceBuilder().get_storage_dir() / f"{session.id}/{package.id}_roi.jpg"
    ).read_bytes()

    response = client.get(f"/api/v1/evidence/{package.id}/roi-image?token={token}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == real_bytes
    # Confirms the frame image and ROI crop are genuinely DIFFERENT files
    # (not the same image served twice under different routes).
    frame_bytes = (
        EvidenceBuilder().get_storage_dir() / f"{session.id}/{package.id}_frame.jpg"
    ).read_bytes()
    assert real_bytes != frame_bytes


def test_evidence_image_with_no_token_returns_401(client, db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)

    response = client.get(f"/api/v1/evidence/{package.id}/frame-image")
    assert response.status_code == 401


def test_evidence_image_with_invalid_token_returns_401(client, db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)

    response = client.get(f"/api/v1/evidence/{package.id}/roi-image?token=garbage-not-a-jwt")
    assert response.status_code == 401


def test_evidence_image_with_token_for_a_different_package_returns_401(
    client, auth_headers, db_session, make_video, test_user
):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package_a = _persist_package(db_session, session)
    package_b = _persist_package(db_session, session)
    token_for_a = _get_evidence_token(client, auth_headers, package_a.id)

    response = client.get(f"/api/v1/evidence/{package_b.id}/frame-image?token={token_for_a}")
    assert response.status_code == 401


def test_one_evidence_token_works_for_both_frame_and_roi_images(
    client, auth_headers, db_session, make_video, test_user
):
    # Resolution 2: ONE token, scoped to one evidence_package_id, is valid
    # for BOTH images — frame vs. roi is determined by which route is hit,
    # not encoded in the token.
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    package = _persist_package(db_session, session)
    token = _get_evidence_token(client, auth_headers, package.id)

    frame_response = client.get(f"/api/v1/evidence/{package.id}/frame-image?token={token}")
    roi_response = client.get(f"/api/v1/evidence/{package.id}/roi-image?token={token}")
    assert frame_response.status_code == 200
    assert roi_response.status_code == 200
