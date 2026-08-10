from app.core.config import settings
from app.models.analysis_session import AnalysisSession, SessionStatus
from app.models.processing_run import ProcessingRun, ProcessingRunStatus


def test_create_session_valid_video(client, auth_headers, make_video, test_user):
    video = make_video()
    user, _ = test_user

    response = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    )
    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["video_id"] == str(video.id)
    assert data["video_original_filename"] == video.original_filename
    assert data["status"] == "CREATED"
    assert data["created_by"] == str(user.id)
    assert data["created_by_email"] == user.email
    assert data["latest_processing_run"] is None
    assert data["model_config"]["detector_model"] == settings.DETECTOR_MODEL
    assert data["model_config"]["detector_runtime"] == settings.DETECTOR_RUNTIME
    assert data["model_config"]["vlm_model"] == settings.VLM_MODEL
    assert data["model_config"]["llm_model"] == settings.LLM_MODEL


def test_create_session_nonexistent_video(client, auth_headers):
    response = client.post(
        "/api/v1/sessions",
        headers=auth_headers,
        json={"video_id": "00000000-0000-0000-0000-000000000000"},
    )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"


def test_create_session_without_auth(client, make_video):
    video = make_video()
    response = client.post("/api/v1/sessions", json={"video_id": str(video.id)})
    assert response.status_code == 401


def test_model_config_is_deduplicated(client, auth_headers, make_video):
    video1 = make_video(original_filename="a.mp4")
    video2 = make_video(original_filename="b.mp4")

    r1 = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video1.id)}
    )
    r2 = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video2.id)}
    )
    assert r1.status_code == 201
    assert r2.status_code == 201
    assert r1.json()["data"]["model_config"]["id"] == r2.json()["data"]["model_config"]["id"]


def test_start_session_from_created(client, auth_headers, make_video, db_session):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]

    response = client.post(f"/api/v1/sessions/{created['id']}/start", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["status"] == "QUEUED"
    assert body["data"]["latest_processing_run"] is not None
    assert body["data"]["latest_processing_run"]["status"] == "PENDING"

    run = (
        db_session.query(ProcessingRun)
        .filter(ProcessingRun.session_id == created["id"])
        .first()
    )
    assert run is not None
    assert run.status == ProcessingRunStatus.PENDING


def test_start_session_already_queued_conflicts(client, auth_headers, make_video):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]
    client.post(f"/api/v1/sessions/{created['id']}/start", headers=auth_headers)

    response = client.post(f"/api/v1/sessions/{created['id']}/start", headers=auth_headers)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_cancel_session_from_created(client, auth_headers, make_video):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]

    response = client.post(f"/api/v1/sessions/{created['id']}/cancel", headers=auth_headers)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "CANCELLED"


def test_cancel_session_from_queued_also_cancels_processing_run(
    client, auth_headers, make_video, db_session
):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]
    client.post(f"/api/v1/sessions/{created['id']}/start", headers=auth_headers)

    response = client.post(f"/api/v1/sessions/{created['id']}/cancel", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["data"]["status"] == "CANCELLED"
    assert body["data"]["latest_processing_run"]["status"] == "CANCELLED"
    assert body["data"]["latest_processing_run"]["completed_at"] is not None

    run = (
        db_session.query(ProcessingRun)
        .filter(ProcessingRun.session_id == created["id"])
        .first()
    )
    assert run.status == ProcessingRunStatus.CANCELLED
    assert run.completed_at is not None


def test_cancel_session_already_cancelled_conflicts(client, auth_headers, make_video):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]
    client.post(f"/api/v1/sessions/{created['id']}/cancel", headers=auth_headers)

    response = client.post(f"/api/v1/sessions/{created['id']}/cancel", headers=auth_headers)
    assert response.status_code == 409
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_STATE_TRANSITION"


def test_list_sessions_respects_filters_and_pagination(client, auth_headers, make_video):
    video1 = make_video(original_filename="a.mp4")
    video2 = make_video(original_filename="b.mp4")

    ids = []
    for video in (video1, video1, video2):
        created = client.post(
            "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
        ).json()["data"]
        ids.append(created["id"])

    # Cancel one of the video1 sessions so we have a mix of statuses.
    client.post(f"/api/v1/sessions/{ids[0]}/cancel", headers=auth_headers)

    response = client.get("/api/v1/sessions", headers=auth_headers)
    body = response.json()
    assert body["data"]["total"] == 3

    response = client.get(
        f"/api/v1/sessions?video_id={video1.id}", headers=auth_headers
    )
    body = response.json()
    assert body["data"]["total"] == 2
    assert all(item["video_id"] == str(video1.id) for item in body["data"]["items"])

    response = client.get("/api/v1/sessions?status=CANCELLED", headers=auth_headers)
    body = response.json()
    assert body["data"]["total"] == 1
    assert body["data"]["items"][0]["status"] == "CANCELLED"

    response = client.get("/api/v1/sessions?limit=1&offset=1", headers=auth_headers)
    body = response.json()
    assert body["data"]["limit"] == 1
    assert body["data"]["offset"] == 1
    assert len(body["data"]["items"]) == 1


def test_get_session_not_found(client, auth_headers):
    response = client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_get_session_status_lightweight_shape(client, auth_headers, make_video):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]

    response = client.get(
        f"/api/v1/sessions/{created['id']}/status", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    data = body["data"]
    assert set(data.keys()) == {"id", "status", "latest_processing_run"}
    assert data["id"] == created["id"]
    assert data["status"] == "CREATED"
    assert data["latest_processing_run"] is None

    client.post(f"/api/v1/sessions/{created['id']}/start", headers=auth_headers)
    response = client.get(
        f"/api/v1/sessions/{created['id']}/status", headers=auth_headers
    )
    data = response.json()["data"]
    assert data["status"] == "QUEUED"
    assert data["latest_processing_run"]["status"] == "PENDING"
