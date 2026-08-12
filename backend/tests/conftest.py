import uuid
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import Settings, settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User
from app.models.video import VideoAsset
from tests.fixtures.synthetic_video import (
    DEFAULT_FPS,
    DEFAULT_HEIGHT,
    DEFAULT_NUM_FRAMES,
    DEFAULT_WIDTH,
    generate_synthetic_mp4,
)

# Path to a 1000-byte hand-crafted MP4 fixture: a 32-byte "ftyp" box (size
# field 0x00000020, type "ftyp", major_brand "isom", minor_version 0x200,
# compatible_brands "isomiso2avc1mp41") followed by 968 zero-byte padding.
# validate_mp4_magic_bytes() only inspects offset 4-7 ("ftyp"), so the
# padding content is arbitrary — it just gives the fixture a known,
# non-trivial total size for file-size assertions in tests.
#
# IMPORTANT (Phase 5): this file has valid magic bytes but is NOT a
# genuinely decodable video (no real frame data, just padding) — it is now
# correctly rejected by the deeper OpenCV-based validation added in Phase 5.
# It is kept around specifically to test that rejection path (see
# test_upload_old_fixture_now_rejected_as_unreadable in test_videos.py); the
# "valid upload" tests use `synthetic_mp4_path` below instead. See
# backend/tests/fixtures/ for the file itself.
VALID_MP4_FIXTURE = Path(__file__).parent / "fixtures" / "valid_minimal.mp4"

# Tests use a dedicated database (DATABASE_URL_TEST) and create/drop tables
# directly via Base.metadata instead of running Alembic migrations. This is a
# pragmatic testing shortcut for speed/simplicity — not a production pattern;
# the real schema is still owned by Alembic (see backend/alembic/).
test_engine = create_engine(settings.DATABASE_URL_TEST)
TestSessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=test_engine)


@pytest.fixture(scope="session", autouse=True)
def _create_test_schema():
    Base.metadata.create_all(bind=test_engine)
    yield
    Base.metadata.drop_all(bind=test_engine)


@pytest.fixture
def db_session():
    session = TestSessionLocal()
    try:
        yield session
    finally:
        for table in reversed(Base.metadata.sorted_tables):
            session.execute(table.delete())
        session.commit()
        session.close()


@pytest.fixture
def client(db_session):
    def _override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = _override_get_db
    yield TestClient(app)
    app.dependency_overrides.clear()


@pytest.fixture
def make_user(db_session):
    def _make_user(
        email: str, password: str, role: Role = Role.OPERATOR, full_name: str | None = None
    ) -> User:
        user = User(
            email=email,
            hashed_password=hash_password(password),
            full_name=full_name,
            role=role,
        )
        db_session.add(user)
        db_session.commit()
        db_session.refresh(user)
        return user

    return _make_user


@pytest.fixture
def test_user(make_user) -> tuple[User, str]:
    password = "testpass123"
    user = make_user("operator@example.com", password, role=Role.OPERATOR, full_name="Test Operator")
    return user, password


@pytest.fixture(autouse=True)
def _video_storage_tmp(tmp_path, monkeypatch):
    # Redirects VIDEO_STORAGE_PATH to a per-test temp directory so tests never
    # touch (or depend on) the real storage/videos/ directory. Autouse since
    # any test exercising the video endpoints needs this, and it's a no-op
    # for tests that don't.
    monkeypatch.setattr(settings, "VIDEO_STORAGE_PATH", str(tmp_path / "videos"))


@pytest.fixture(autouse=True)
def _heatmap_storage_tmp(tmp_path, monkeypatch):
    # Same test-isolation pattern as _video_storage_tmp above (Phase 3),
    # extended to Phase 12's HEATMAP_STORAGE_PATH — tests must never write
    # to the developer's real storage/heatmaps/.
    monkeypatch.setattr(settings, "HEATMAP_STORAGE_PATH", str(tmp_path / "heatmaps"))


@pytest.fixture(autouse=True)
def _risk_thresholds_from_code_defaults(monkeypatch):
    # Phase 13: RISK_ELEVATED_THRESHOLD / RISK_CRITICAL_THRESHOLD /
    # RISK_INCIDENT_THRESHOLD are PRE-EXISTING keys (Phase 1 placeholders,
    # 0-1 scale) that the developer's real .env already sets — pydantic-
    # settings' env-file source takes precedence over config.py's class
    # defaults, so the real, calibrated, 0-100-scale defaults this phase
    # authored are silently shadowed by the stale placeholders UNLESS the
    # developer manually updates their local .env (not done here — this
    # project never edits the developer's real .env, see DECISIONS.md).
    # Forcing the class defaults here (not the possibly-stale live
    # `settings` values) keeps Phase 13's tests deterministic and correct
    # regardless of local .env drift, exactly like _video_storage_tmp/
    # _heatmap_storage_tmp above shield tests from the developer's real
    # storage paths.
    for field_name in (
        "RISK_ELEVATED_THRESHOLD",
        "RISK_CRITICAL_THRESHOLD",
        "RISK_INCIDENT_THRESHOLD",
        "RISK_STATE_FALL_HYSTERESIS_MARGIN",
        "RISK_STATE_PERSISTENCE_FRAMES",
        "VLM_COOLDOWN",
        "FALLBACK_ANALYSIS_INTERVAL",
    ):
        monkeypatch.setattr(settings, field_name, Settings.model_fields[field_name].default)


@pytest.fixture(autouse=True)
def _vlm_model_from_code_default(monkeypatch):
    # Phase 14: VLM_MODEL is ANOTHER pre-existing key (Phase 1 placeholder
    # "placeholder-vlm") that the developer's real .env already sets — same
    # stale-.env-shadowing issue as _risk_thresholds_from_code_defaults
    # above, now affecting Vision Intelligence: without this override,
    # every real-inference test in test_minicpm_vlm.py would try to call
    # Ollama with model="placeholder-vlm" (never pulled, doesn't exist) and
    # skip/fail instead of exercising real inference against the actually-
    # pulled minicpm-v4.6:q4_K_M tag.
    monkeypatch.setattr(settings, "VLM_MODEL", Settings.model_fields["VLM_MODEL"].default)


@pytest.fixture
def make_video(db_session, test_user):
    def _make_video(
        original_filename: str = "clip.mp4",
        storage_filename: str | None = None,
        file_size_bytes: int = 1000,
        uploader: User | None = None,
    ) -> VideoAsset:
        uploaded_by = uploader.id if uploader else test_user[0].id
        video = VideoAsset(
            original_filename=original_filename,
            storage_filename=storage_filename or f"{uuid.uuid4()}.mp4",
            file_size_bytes=file_size_bytes,
            mime_type="video/mp4",
            uploaded_by=uploaded_by,
        )
        db_session.add(video)
        db_session.commit()
        db_session.refresh(video)
        return video

    return _make_video


@pytest.fixture
def synthetic_mp4_path(tmp_path) -> Path:
    """A genuinely OpenCV-decodable MP4 generated fresh per test (10 frames,
    64x64, 10fps) — see tests/fixtures/synthetic_video.py."""
    path = tmp_path / "synthetic.mp4"
    generate_synthetic_mp4(path)
    return path


@pytest.fixture
def auth_headers(client, test_user):
    user, password = test_user
    response = client.post(
        "/api/v1/auth/verify-credentials",
        json={"email": user.email, "password": password},
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
