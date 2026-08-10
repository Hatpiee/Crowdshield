import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from app.core.config import settings
from app.core.database import Base, get_db
from app.core.security import hash_password
from app.main import app
from app.models.user import Role, User

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
