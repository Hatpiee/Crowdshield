from pathlib import Path

from app.core.config import settings
from app.models.video import VideoAsset
from tests.conftest import VALID_MP4_FIXTURE


def _storage_files() -> list[Path]:
    storage_dir = Path(settings.VIDEO_STORAGE_PATH)
    if not storage_dir.exists():
        return []
    return list(storage_dir.iterdir())


def test_upload_valid_mp4(client, auth_headers, db_session):
    fixture_bytes = VALID_MP4_FIXTURE.read_bytes()

    response = client.post(
        "/api/v1/videos",
        headers=auth_headers,
        files={"file": ("clip.mp4", fixture_bytes, "video/mp4")},
    )
    assert response.status_code == 201

    body = response.json()
    assert body["success"] is True
    data = body["data"]
    assert data["original_filename"] == "clip.mp4"
    assert data["file_size_bytes"] == len(fixture_bytes)
    assert data["mime_type"] == "video/mp4"
    assert "storage_filename" not in data
    assert "storage_filename" not in str(body)

    files_on_disk = _storage_files()
    assert len(files_on_disk) == 1
    assert files_on_disk[0].suffix == ".mp4"
    assert files_on_disk[0].stat().st_size == len(fixture_bytes)

    row = db_session.query(VideoAsset).filter(VideoAsset.id == data["id"]).first()
    assert row is not None
    assert row.original_filename == "clip.mp4"
    assert row.file_size_bytes == len(fixture_bytes)
    assert row.storage_filename == files_on_disk[0].name


def test_upload_without_authorization_header(client):
    fixture_bytes = VALID_MP4_FIXTURE.read_bytes()
    response = client.post(
        "/api/v1/videos",
        files={"file": ("clip.mp4", fixture_bytes, "video/mp4")},
    )
    assert response.status_code == 401


def test_upload_wrong_extension(client, auth_headers):
    response = client.post(
        "/api/v1/videos",
        headers=auth_headers,
        files={"file": ("clip.avi", b"irrelevant content", "video/mp4")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_FILE_TYPE"


def test_upload_fails_magic_byte_check(client, auth_headers):
    fake_content = b"This is a plain text file renamed to look like an mp4." * 10
    response = client.post(
        "/api/v1/videos",
        headers=auth_headers,
        files={"file": ("fake.mp4", fake_content, "video/mp4")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "INVALID_MP4_CONTENT"
    assert _storage_files() == []


def test_upload_exceeding_max_size(client, auth_headers, monkeypatch):
    monkeypatch.setattr(settings, "MAX_UPLOAD_SIZE_MB", 0)
    fixture_bytes = VALID_MP4_FIXTURE.read_bytes()

    response = client.post(
        "/api/v1/videos",
        headers=auth_headers,
        files={"file": ("clip.mp4", fixture_bytes, "video/mp4")},
    )
    assert response.status_code == 400
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "FILE_TOO_LARGE"
    assert _storage_files() == []


def test_upload_missing_file_field_returns_envelope_shaped_422(client, auth_headers):
    response = client.post("/api/v1/videos", headers=auth_headers)
    assert response.status_code == 422

    body = response.json()
    assert body["success"] is False
    assert body["data"] is None
    assert body["error"]["code"] == "VALIDATION_ERROR"
    assert isinstance(body["error"]["message"], str)
    assert isinstance(body["meta"]["request_id"], str)
    assert "detail" not in body


def test_list_videos_respects_limit_and_offset(client, auth_headers):
    fixture_bytes = VALID_MP4_FIXTURE.read_bytes()
    for i in range(3):
        response = client.post(
            "/api/v1/videos",
            headers=auth_headers,
            files={"file": (f"clip{i}.mp4", fixture_bytes, "video/mp4")},
        )
        assert response.status_code == 201

    response = client.get("/api/v1/videos", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["total"] == 3
    assert len(body["data"]["items"]) == 3
    # Most recently uploaded first.
    assert body["data"]["items"][0]["original_filename"] == "clip2.mp4"

    response = client.get("/api/v1/videos?limit=1&offset=1", headers=auth_headers)
    body = response.json()
    assert body["data"]["limit"] == 1
    assert body["data"]["offset"] == 1
    assert len(body["data"]["items"]) == 1
    assert body["data"]["items"][0]["original_filename"] == "clip1.mp4"


def test_get_video_by_id(client, auth_headers, test_user):
    fixture_bytes = VALID_MP4_FIXTURE.read_bytes()
    upload = client.post(
        "/api/v1/videos",
        headers=auth_headers,
        files={"file": ("clip.mp4", fixture_bytes, "video/mp4")},
    )
    video_id = upload.json()["data"]["id"]
    user, _ = test_user

    response = client.get(f"/api/v1/videos/{video_id}", headers=auth_headers)
    assert response.status_code == 200
    body = response.json()
    assert body["success"] is True
    assert body["data"]["id"] == video_id
    assert body["data"]["uploaded_by_email"] == user.email
    assert "storage_filename" not in body["data"]
    assert "storage_filename" not in str(body)


def test_get_video_by_id_not_found(client, auth_headers):
    response = client.get(
        "/api/v1/videos/00000000-0000-0000-0000-000000000000", headers=auth_headers
    )
    assert response.status_code == 404
    body = response.json()
    assert body["success"] is False
    assert body["error"]["code"] == "NOT_FOUND"
