from app.core.config import settings
from app.models.heatmap import HeatmapSnapshot, HeatmapType
from app.services import heatmap_service, session_service
from tests.fixtures.crowd_metrics_builder import FRAME_HEIGHT, FRAME_WIDTH, make_crowd_metrics


def _make_session_with_snapshots(db_session, make_video, test_user, with_projection=True):
    video = make_video()
    user, _ = test_user
    session = session_service.create_session(db_session, video.id, user.id)
    crowd_metrics = make_crowd_metrics(
        frame_number=5, timestamp_seconds=0.166, with_projection=with_projection
    )
    heatmap_service.generate_and_persist_heatmaps(
        db_session, session.id, 5, 0.166, crowd_metrics, FRAME_WIDTH, FRAME_HEIGHT
    )
    return session


def test_list_heatmaps_without_auth_returns_401(client, db_session, make_video, test_user):
    session = _make_session_with_snapshots(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/heatmaps")
    assert response.status_code == 401


def test_list_heatmaps_returns_generated_snapshots_correct_envelope(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_snapshots(db_session, make_video, test_user)

    response = client.get(f"/api/v1/sessions/{session.id}/heatmaps", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True

    items = body["data"]["items"]
    assert len(items) == 5
    types = {item["heatmap_type"] for item in items}
    assert types == {"DENSITY", "PRESSURE", "FLOW_CONGESTION", "RISK", "PREDICTIVE"}

    for item in items:
        # file_path is an internal storage detail — never exposed via the API
        # (same reasoning as VideoRead excluding storage_filename, Phase 3).
        assert "file_path" not in item
        assert set(item.keys()) == {
            "id",
            "heatmap_type",
            "frame_number",
            "timestamp_seconds",
            "file_size_bytes",
            "created_at",
        }


def test_list_heatmaps_filters_by_type_query_param(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_snapshots(db_session, make_video, test_user)

    response = client.get(
        f"/api/v1/sessions/{session.id}/heatmaps?heatmap_type=DENSITY", headers=auth_headers
    )
    assert response.status_code == 200
    items = response.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["heatmap_type"] == "DENSITY"


def test_get_latest_heatmap_valid_type_and_existing_snapshot(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_snapshots(db_session, make_video, test_user)

    response = client.get(
        f"/api/v1/sessions/{session.id}/heatmaps/DENSITY", headers=auth_headers
    )
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["heatmap_type"] == "DENSITY"
    assert body["data"]["frame_number"] == 5
    assert "file_path" not in body["data"]


def test_get_latest_heatmap_invalid_type_returns_400(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_snapshots(db_session, make_video, test_user)

    response = client.get(
        f"/api/v1/sessions/{session.id}/heatmaps/NOT_A_REAL_TYPE", headers=auth_headers
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_HEATMAP_TYPE"


def test_get_latest_heatmap_valid_type_but_no_snapshot_returns_404(
    client, auth_headers, db_session, make_video, test_user
):
    # with_projection=False -> no PREDICTIVE snapshot was ever generated for
    # this session (per the skip-when-unavailable rule) — a genuinely
    # correct 404, not a bug.
    session = _make_session_with_snapshots(db_session, make_video, test_user, with_projection=False)

    response = client.get(
        f"/api/v1/sessions/{session.id}/heatmaps/PREDICTIVE", headers=auth_headers
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_list_heatmaps_nonexistent_session_returns_404(client, auth_headers):
    response = client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/heatmaps",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


def test_get_latest_heatmap_nonexistent_session_returns_404(client, auth_headers):
    response = client.get(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/heatmaps/DENSITY",
        headers=auth_headers,
    )
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "NOT_FOUND"


# Phase 22, Step 2/3: heatmap-image-serving route — access-token issuance +
# token-gated byte serving, real image bytes, and auth-failure cases.
# Mirrors test_video_streaming.py's own coverage shape for the analogous
# video route.


def _get_density_snapshot(db_session, session) -> HeatmapSnapshot:
    return (
        db_session.query(HeatmapSnapshot)
        .filter(
            HeatmapSnapshot.session_id == session.id,
            HeatmapSnapshot.heatmap_type == HeatmapType.DENSITY,
        )
        .one()
    )


def _get_image_token(client, auth_headers, heatmap_id) -> str:
    response = client.get(f"/api/v1/heatmaps/{heatmap_id}/access-token", headers=auth_headers)
    assert response.status_code == 200
    return response.json()["data"]["token"]


def test_heatmap_access_token_route_requires_auth(client, db_session, make_video, test_user):
    session = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot = _get_density_snapshot(db_session, session)

    response = client.get(f"/api/v1/heatmaps/{snapshot.id}/access-token")
    assert response.status_code == 401


def test_heatmap_access_token_route_returns_a_real_token(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot = _get_density_snapshot(db_session, session)

    response = client.get(f"/api/v1/heatmaps/{snapshot.id}/access-token", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"]["token"], str) and body["data"]["token"]
    assert body["data"]["expires_in_seconds"] > 0


def test_heatmap_access_token_route_nonexistent_heatmap_returns_404(client, auth_headers):
    response = client.get(
        "/api/v1/heatmaps/00000000-0000-0000-0000-000000000000/access-token",
        headers=auth_headers,
    )
    assert response.status_code == 404


def test_heatmap_image_served_with_valid_token_returns_real_jpeg_bytes(
    client, auth_headers, db_session, make_video, test_user
):
    session = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot = _get_density_snapshot(db_session, session)
    token = _get_image_token(client, auth_headers, snapshot.id)

    real_bytes = (heatmap_service.get_storage_dir() / snapshot.file_path).read_bytes()

    response = client.get(f"/api/v1/heatmaps/{snapshot.id}/image?token={token}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "image/jpeg"
    assert response.content == real_bytes
    assert len(response.content) == snapshot.file_size_bytes


def test_heatmap_image_with_no_token_returns_401(client, db_session, make_video, test_user):
    session = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot = _get_density_snapshot(db_session, session)

    response = client.get(f"/api/v1/heatmaps/{snapshot.id}/image")
    assert response.status_code == 401


def test_heatmap_image_with_invalid_token_returns_401(client, db_session, make_video, test_user):
    session = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot = _get_density_snapshot(db_session, session)

    response = client.get(f"/api/v1/heatmaps/{snapshot.id}/image?token=garbage-not-a-jwt")
    assert response.status_code == 401


def test_heatmap_image_with_expired_token_returns_401(
    client, auth_headers, db_session, make_video, test_user, monkeypatch
):
    from app.core.stream_token import generate_heatmap_access_token

    session = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot = _get_density_snapshot(db_session, session)

    monkeypatch.setattr(settings, "HEATMAP_TOKEN_EXPIRE_MINUTES", -1)
    expired_token = generate_heatmap_access_token(snapshot.id, snapshot.session_id)

    response = client.get(f"/api/v1/heatmaps/{snapshot.id}/image?token={expired_token}")
    assert response.status_code == 401


def test_heatmap_image_with_token_for_a_different_heatmap_returns_401(
    client, auth_headers, db_session, make_video, test_user
):
    session_a = _make_session_with_snapshots(db_session, make_video, test_user)
    session_b = _make_session_with_snapshots(db_session, make_video, test_user)
    snapshot_a = _get_density_snapshot(db_session, session_a)
    snapshot_b = _get_density_snapshot(db_session, session_b)
    token_for_a = _get_image_token(client, auth_headers, snapshot_a.id)

    response = client.get(f"/api/v1/heatmaps/{snapshot_b.id}/image?token={token_for_a}")
    assert response.status_code == 401


def test_heatmap_image_nonexistent_heatmap_returns_404(client, auth_headers, db_session, make_video, test_user):
    from app.core.stream_token import generate_heatmap_access_token

    fake_id = "00000000-0000-0000-0000-000000000000"
    token = generate_heatmap_access_token(fake_id, "00000000-0000-0000-0000-000000000000")

    response = client.get(f"/api/v1/heatmaps/{fake_id}/image?token={token}")
    assert response.status_code == 404
