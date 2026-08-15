"""Phase 21, Step 6: GET /system/status — 3 real, independent checks."""

from sqlalchemy import text

from app.api import system as system_module
from app.core.config import settings
from app.models.analysis_session import SessionStatus
from app.services import session_service


def test_system_status_requires_auth(client):
    response = client.get("/api/v1/system/status")
    assert response.status_code == 401


def test_system_status_all_three_checks_present_and_ok_against_real_infra(client, auth_headers):
    # Real DB (the test database) and real Ollama (this dev environment's
    # own running daemon) are both genuinely reachable — no mocking.
    response = client.get("/api/v1/system/status", headers=auth_headers)
    assert response.status_code == 200
    data = response.json()["data"]

    assert data["database"]["status"] == "ok"
    assert data["ollama"]["status"] == "ok"
    assert data["processing_sessions"]["status"] == "ok"
    assert isinstance(data["processing_sessions"]["count"], int)


def test_processing_sessions_count_reflects_a_real_processing_session(
    client, auth_headers, db_session, make_video, test_user
):
    video = make_video()
    session = session_service.create_session(db_session, video.id, test_user[0].id)
    session.status = SessionStatus.PROCESSING
    db_session.commit()

    response = client.get("/api/v1/system/status", headers=auth_headers)
    data = response.json()["data"]
    assert data["processing_sessions"]["count"] >= 1


def test_check_database_reports_degraded_on_a_real_query_failure(db_session):
    # A genuine failed statement (not a table this schema has) aborts the
    # session's transaction — the SAME real condition _check_database's
    # own "SELECT 1" would then hit.
    try:
        db_session.execute(text("SELECT * FROM this_table_does_not_exist"))
    except Exception:
        pass

    result = system_module._check_database(db_session)
    assert result["status"] == "degraded"
    db_session.rollback()


def test_check_ollama_reports_degraded_when_genuinely_unreachable(monkeypatch):
    # A real connection attempt against a port nothing listens on — not a
    # mock of the exception-handling logic itself.
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://127.0.0.1:1")
    result = system_module._check_ollama()
    assert result["status"] == "degraded"


def test_database_outage_does_not_affect_the_independent_ollama_check(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        system_module,
        "_check_database",
        lambda db: {"status": "degraded", "detail": "simulated DB outage"},
    )

    response = client.get("/api/v1/system/status", headers=auth_headers)
    data = response.json()["data"]
    assert data["database"]["status"] == "degraded"
    # Proves independence: the Ollama check's real result is UNAFFECTED.
    assert data["ollama"]["status"] == "ok"


def test_ollama_outage_does_not_affect_the_independent_database_check(
    client, auth_headers, monkeypatch
):
    monkeypatch.setattr(
        system_module,
        "_check_ollama",
        lambda: {"status": "degraded", "detail": "simulated Ollama outage"},
    )

    response = client.get("/api/v1/system/status", headers=auth_headers)
    data = response.json()["data"]
    assert data["ollama"]["status"] == "degraded"
    # Proves independence: the database check's real result is UNAFFECTED.
    assert data["database"]["status"] == "ok"
