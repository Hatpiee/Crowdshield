from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User

# Path to a 1000-byte hand-crafted MP4 fixture: a 32-byte "ftyp" box (size
# field 0x00000020, type "ftyp", major_brand "isom", minor_version 0x200,
# compatible_brands "isomiso2avc1mp41") followed by 968 zero-byte padding.
# validate_mp4_magic_bytes() only inspects offset 4-7 ("ftyp"), so the
# padding content is arbitrary — it just gives the fixture a known,
# non-trivial total size for file-size assertions in tests. See
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


@pytest.fixture
def auth_headers(client, test_user):
    user, password = test_user
    response = client.post(
        "/api/v1/auth/verify-credentials",
        json={"email": user.email, "password": password},
    )
    token = response.json()["data"]["access_token"]
    return {"Authorization": f"Bearer {token}"}
