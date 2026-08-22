"""Final Intelligence phase: session_copilot.py tests. No real Ollama calls
except the one dedicated real-inference test at the bottom (matches this
project's `test_real_*` convention and Phase O's "small number of real
calls, not dozens" instruction).
"""

import json
import uuid

from app.pipeline.session_copilot import (
    CopilotAnswerResult,
    _MAX_CONTEXT_EVENTS,
    _serialize_session_context,
    suggested_questions,
)
from app.pipeline.session_report import (
    EventSummary,
    RiskOverview,
    SessionReportResult,
)


def _event(session_marker: str, t: float, status: str = "OBSERVED") -> EventSummary:
    return EventSummary(
        evidence_package_id=uuid.uuid4(), frame_number=int(t * 30), timestamp_seconds=t,
        trigger_type="RISK", trigger_reason="test", status=status, decision_outcome=None,
        event_classification=None, onset_seconds=None, peak_seconds=t, duration_seconds=None,
        severity_tag="LOW", confidence=0.8, location_description="center of frame",
        description=f"SECRET_MARKER_{session_marker}", observation_categories=[],
    )


def _report(session_id: uuid.UUID, marker: str, event_count: int = 1, incident_count: int = 0) -> SessionReportResult:
    events = [_event(marker, float(i)) for i in range(event_count)]
    return SessionReportResult(
        session_id=session_id, session_status="COMPLETED", video_filename=f"{marker}.mp4",
        video_duration_seconds=10.0,
        risk_overview=RiskOverview(current_state=None, current_score=None, trend="UNKNOWN", trend_delta=None, trend_window_seconds=None),
        investigated_event_count=event_count, confirmed_incident_count=incident_count, events=events,
        overview_summary=f"overview for {marker}", incidents_summary="none",
    )


def test_serialized_context_never_leaks_another_sessions_data():
    session_a = uuid.uuid4()
    session_b = uuid.uuid4()
    report_a = _report(session_a, "SESSION_A")
    report_b = _report(session_b, "SESSION_B")

    context_a = _serialize_session_context(report_a)

    # The operator could ask a question mentioning session B's id/marker —
    # but the context builder only ever receives report_a, built solely
    # from session_a's own path parameter. Prove session B's data is
    # structurally absent from what gets sent to the LLM.
    assert str(session_b) not in context_a
    assert "SESSION_B" not in context_a
    assert str(session_a) in context_a
    assert "SESSION_A" in context_a
    # Sanity: report_b really does carry different data (not a no-op test).
    context_b = _serialize_session_context(report_b)
    assert context_a != context_b


def test_serialized_context_bounds_event_count():
    session_id = uuid.uuid4()
    report = _report(session_id, "MANY", event_count=_MAX_CONTEXT_EVENTS + 20)

    context = json.loads(_serialize_session_context(report))

    assert len(context["events"]) == _MAX_CONTEXT_EVENTS


def test_serialized_context_includes_events_vs_incidents_distinction():
    session_id = uuid.uuid4()
    report = _report(session_id, "X", event_count=3, incident_count=1)

    context = json.loads(_serialize_session_context(report))

    assert context["investigated_event_count"] == 3
    assert context["confirmed_incident_count"] == 1
    assert "is_confirmed_incident" in context["events"][0]


def test_suggested_questions_zero_events_session():
    session_id = uuid.uuid4()
    report = _report(session_id, "EMPTY", event_count=0, incident_count=0)
    report.events = []
    report.investigated_event_count = 0

    questions = suggested_questions(report)

    assert "What was the most serious event?" not in questions
    assert len(questions) > 0


def test_suggested_questions_with_events_and_incidents():
    session_id = uuid.uuid4()
    report = _report(session_id, "BUSY", event_count=3, incident_count=1)

    questions = suggested_questions(report)

    assert "What was the most serious event?" in questions
    assert "Were any incidents confirmed?" in questions
    assert "Why weren't any incidents confirmed?" not in questions


# ============================================================
# API-level tests (mocked SessionCopilot — no real Ollama call)
# ============================================================

def test_suggested_questions_route(client, auth_headers, make_video):
    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]

    response = client.get(
        f"/api/v1/sessions/{created['id']}/copilot/suggested-questions", headers=auth_headers
    )
    assert response.status_code == 200
    assert len(response.json()["data"]["questions"]) > 0


def test_ask_copilot_route_not_found(client, auth_headers):
    response = client.post(
        "/api/v1/sessions/00000000-0000-0000-0000-000000000000/copilot/ask",
        headers=auth_headers, json={"question": "What happened?"},
    )
    assert response.status_code == 404


def test_ask_copilot_route_returns_grounded_answer(client, auth_headers, make_video, monkeypatch):
    import app.api.copilot as copilot_api

    class _FakeCopilot:
        def __init__(self):
            pass

        def ask(self, question, report):
            return CopilotAnswerResult(answer="No incidents were confirmed for this session.", cited_timestamps=[1.0])

    monkeypatch.setattr(copilot_api, "SessionCopilot", _FakeCopilot)

    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]

    response = client.post(
        f"/api/v1/sessions/{created['id']}/copilot/ask",
        headers=auth_headers, json={"question": "Were there any incidents?"},
    )
    assert response.status_code == 200
    data = response.json()["data"]
    assert data["answer"] == "No incidents were confirmed for this session."
    assert data["cited_timestamps"] == [1.0]


def test_ask_copilot_route_unavailable_returns_503(client, auth_headers, make_video, monkeypatch):
    import app.api.copilot as copilot_api
    from app.pipeline.session_copilot import CopilotUnavailableError

    class _FakeCopilot:
        def __init__(self):
            raise CopilotUnavailableError("Ollama unreachable")

    monkeypatch.setattr(copilot_api, "SessionCopilot", _FakeCopilot)

    video = make_video()
    created = client.post(
        "/api/v1/sessions", headers=auth_headers, json={"video_id": str(video.id)}
    ).json()["data"]

    response = client.post(
        f"/api/v1/sessions/{created['id']}/copilot/ask",
        headers=auth_headers, json={"question": "What happened?"},
    )
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "COPILOT_UNAVAILABLE"


def test_ask_copilot_cannot_retrieve_other_sessions_data(client, auth_headers, make_video, monkeypatch, db_session, test_user):
    """Phase H mandatory isolation test: session A's Copilot call must never
    surface session B's real events, even when the operator's question text
    explicitly names session B."""
    import app.api.copilot as copilot_api

    captured_reports = []

    class _FakeCopilot:
        def __init__(self):
            pass

        def ask(self, question, report):
            captured_reports.append(report)
            return CopilotAnswerResult(answer="answer", cited_timestamps=[])

    monkeypatch.setattr(copilot_api, "SessionCopilot", _FakeCopilot)

    from app.pipeline.decision_result import DecisionOutcome
    from app.services import session_service
    from tests.test_incident_correlation import _persist_pair

    user, _ = test_user
    video_a = make_video()
    video_b = make_video()
    session_a = session_service.create_session(db_session, video_a.id, user.id)
    session_b = session_service.create_session(db_session, video_b.id, user.id)
    _persist_pair(db_session, session_b, timestamp_seconds=1.0, outcome=DecisionOutcome.INCIDENT)

    response = client.post(
        f"/api/v1/sessions/{session_a.id}/copilot/ask",
        headers=auth_headers, json={"question": f"What happened in session {session_b.id}?"},
    )
    assert response.status_code == 200
    assert len(captured_reports) == 1
    assert captured_reports[0].session_id == session_a.id
    assert captured_reports[0].events == []  # session B's real INCIDENT event never appears


# ============================================================
# Real inference (one dedicated test, per Phase O's "small number of real
# calls, not dozens" instruction — this is the only real Qwen3-8B call in
# this file)
# ============================================================

def test_real_ask_returns_schema_valid_grounded_answer():
    from app.pipeline.session_copilot import SessionCopilot

    session_id = uuid.uuid4()
    report = _report(session_id, "REAL_TEST", event_count=2, incident_count=1)
    report.risk_overview.current_state = "ELEVATED"
    report.risk_overview.current_score = 55.0
    report.risk_overview.trend = "RISING"
    report.overview_summary = "Risk is currently ELEVATED (55.0/100) and has been rising."

    copilot = SessionCopilot()
    result = copilot.ask("Were any incidents confirmed for this session?", report)

    assert isinstance(result, CopilotAnswerResult)
    assert isinstance(result.answer, str) and len(result.answer) > 0
    assert isinstance(result.cited_timestamps, list)
