"""Phase 21, Step 6: real video streaming — stream-token issuance, full-file
GET, real Range-request byte-slice verification, and auth-failure cases."""

from app.core.stream_token import generate_stream_token
from app.services import video_storage


def _get_token(client, auth_headers, video_id) -> str:
    response = client.get(f"/api/v1/videos/{video_id}/stream-token", headers=auth_headers)
    assert response.status_code == 200
    return response.json()["data"]["stream_token"]


def test_stream_token_route_requires_auth(client, make_processable_video):
    video = make_processable_video()
    response = client.get(f"/api/v1/videos/{video.id}/stream-token")
    assert response.status_code == 401


def test_stream_token_route_returns_a_real_token(client, auth_headers, make_processable_video):
    video = make_processable_video()
    response = client.get(f"/api/v1/videos/{video.id}/stream-token", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert isinstance(body["data"]["stream_token"], str) and body["data"]["stream_token"]
    assert body["data"]["expires_in_seconds"] > 0


def test_stream_full_file_no_range_returns_200_with_correct_content_type(
    client, auth_headers, make_processable_video
):
    video = make_processable_video()
    token = _get_token(client, auth_headers, video.id)

    response = client.get(f"/api/v1/videos/{video.id}/stream?token={token}")
    assert response.status_code == 200
    assert response.headers["content-type"] == "video/mp4"

    real_bytes = (video_storage.get_storage_dir() / video.storage_filename).read_bytes()
    assert response.content == real_bytes


def test_stream_with_range_header_returns_206_with_correct_byte_slice(
    client, auth_headers, make_processable_video
):
    video = make_processable_video()
    token = _get_token(client, auth_headers, video.id)
    real_bytes = (video_storage.get_storage_dir() / video.storage_filename).read_bytes()

    start, end = 10, 99  # HTTP Range end is INCLUSIVE
    response = client.get(
        f"/api/v1/videos/{video.id}/stream?token={token}",
        headers={"Range": f"bytes={start}-{end}"},
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{end}/{len(real_bytes)}"
    assert response.headers["accept-ranges"] == "bytes"

    expected_slice = real_bytes[start : end + 1]
    assert response.content == expected_slice
    assert len(response.content) == end - start + 1
    # Not a coincidence: prove this ISN'T just the full file by construction.
    assert response.content != real_bytes


def test_stream_with_open_ended_range_returns_correct_tail_slice(
    client, auth_headers, make_processable_video
):
    video = make_processable_video()
    token = _get_token(client, auth_headers, video.id)
    real_bytes = (video_storage.get_storage_dir() / video.storage_filename).read_bytes()

    start = len(real_bytes) - 50
    response = client.get(
        f"/api/v1/videos/{video.id}/stream?token={token}",
        headers={"Range": f"bytes={start}-"},
    )

    assert response.status_code == 206
    assert response.headers["content-range"] == f"bytes {start}-{len(real_bytes) - 1}/{len(real_bytes)}"
    assert response.content == real_bytes[start:]


def test_stream_with_no_token_returns_401(client, make_processable_video):
    video = make_processable_video()
    response = client.get(f"/api/v1/videos/{video.id}/stream")
    assert response.status_code == 401


def test_stream_with_invalid_token_returns_401(client, make_processable_video):
    video = make_processable_video()
    response = client.get(f"/api/v1/videos/{video.id}/stream?token=garbage-not-a-jwt")
    assert response.status_code == 401


def test_stream_with_expired_token_returns_401(client, make_processable_video, monkeypatch):
    from app.core.config import settings

    video = make_processable_video()
    monkeypatch.setattr(settings, "STREAM_TOKEN_EXPIRE_MINUTES", -1)
    expired_token = generate_stream_token(video.id, video.uploaded_by)

    response = client.get(f"/api/v1/videos/{video.id}/stream?token={expired_token}")
    assert response.status_code == 401


def test_stream_with_token_for_a_different_video_returns_401(
    client, auth_headers, make_processable_video
):
    video_a = make_processable_video()
    video_b = make_processable_video()
    token_for_a = _get_token(client, auth_headers, video_a.id)

    response = client.get(f"/api/v1/videos/{video_b.id}/stream?token={token_for_a}")
    assert response.status_code == 401
