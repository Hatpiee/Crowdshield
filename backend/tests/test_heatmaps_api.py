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
