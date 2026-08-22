"""Final Intelligence phase: session_report_service.py tests. Deterministic,
no LLM/VLM calls (matches the module's own "no new inference" contract).
Reuses test_incident_correlation.py's `_persist_pair` helper (same real
EvidenceBuilder -> persist_evidence_package -> persist_decision_result
chain) for realistic fixtures.
"""

from app.pipeline.decision_result import DecisionOutcome
from app.services import (
    incident_service,
    session_report_service,
    session_service,
)

from tests.test_incident_correlation import _persist_pair


def test_zero_events_report_is_meaningful_not_empty(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    report = session_report_service.build_session_report(db_session, session.id)

    assert report is not None
    assert report.investigated_event_count == 0
    assert report.confirmed_incident_count == 0
    assert report.events == []
    assert "No events were investigated" in report.incidents_summary
    assert report.overview_summary  # never blank


def test_unknown_session_returns_none(db_session):
    import uuid

    assert session_report_service.build_session_report(db_session, uuid.uuid4()) is None


def test_watch_only_events_are_distinct_from_incidents(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    _persist_pair(db_session, session, timestamp_seconds=1.0, outcome=DecisionOutcome.WATCH)
    _persist_pair(db_session, session, timestamp_seconds=2.0, outcome=DecisionOutcome.ABSTAIN)

    report = session_report_service.build_session_report(db_session, session.id)

    assert report.investigated_event_count == 2
    assert report.confirmed_incident_count == 0
    statuses = {e.status for e in report.events}
    assert statuses == {"WATCH", "ABSTAINED"}
    assert "did not meet" not in report.incidents_summary  # exact wording check below
    assert "No event met the evidence threshold" in report.incidents_summary
    assert "1 WATCH" in report.incidents_summary
    assert "1 ABSTAINED" in report.incidents_summary


def test_confirmed_incident_marks_event_status_incident_and_links_incident_id(
    db_session, make_video, test_user
):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package, decision = _persist_pair(db_session, session, timestamp_seconds=1.0, outcome=DecisionOutcome.INCIDENT)
    incident = incident_service.correlate_or_create_incident(db_session, session.id, decision, package)

    report = session_report_service.build_session_report(db_session, session.id)

    assert report.confirmed_incident_count == 1
    assert len(report.events) == 1
    event = report.events[0]
    assert event.status == "INCIDENT"
    assert event.incident_id == incident.id
    assert event.severity_tag == "HIGH"
    assert "confirmed incident" in report.incidents_summary


def test_timeline_includes_events_sorted_by_timestamp(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    _persist_pair(db_session, session, timestamp_seconds=5.0, outcome=DecisionOutcome.WATCH)
    _persist_pair(db_session, session, timestamp_seconds=1.0, outcome=DecisionOutcome.ABSTAIN)

    report = session_report_service.build_session_report(db_session, session.id)

    event_entries = [e for e in report.timeline if e.kind == "EVENT"]
    assert len(event_entries) == 2
    assert event_entries[0].timestamp_seconds == 1.0
    assert event_entries[1].timestamp_seconds == 5.0


def test_severity_tag_pure_function():
    from app.services.session_report_service import _severity_tag

    assert _severity_tag("INCIDENT", "ELEVATED") == "CRITICAL"
    assert _severity_tag("INCIDENT", "NORMAL") == "HIGH"
    assert _severity_tag("INCIDENT", None) == "HIGH"
    assert _severity_tag("WATCH", None) == "MODERATE"
    assert _severity_tag("ABSTAINED", None) == "LOW"
    assert _severity_tag("OBSERVED", None) == "LOW"


def test_location_description_pure_function():
    from app.services.session_report_service import _location_description

    assert _location_description([0.0, 0.0, 100.0, 100.0], None, None) == "unknown region"
    assert _location_description([0.0, 0.0, 10.0, 10.0], 300, 300) == "upper-left of frame"
    assert _location_description([140.0, 140.0, 160.0, 160.0], 300, 300) == "center of frame"
    assert _location_description([280.0, 280.0, 290.0, 290.0], 300, 300) == "lower-right of frame"
