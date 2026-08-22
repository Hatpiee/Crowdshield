"""Session Report assembly — Final Intelligence phase (Phase A/C/D/E).
Deterministic, read-only. See `app/pipeline/session_report.py`'s module
docstring for the full "no new inference" rationale. This module is the
ONLY place `SessionReportResult` is assembled — the API route
(`GET /sessions/{id}/report`) and the Operator Copilot's context builder
(`session_copilot.py`) both consume its output rather than re-querying the
same tables independently.
"""

import uuid
from typing import Optional

from sqlalchemy.orm import Session

from app.models.analysis_session import AnalysisSession
from app.models.crowd_metrics_snapshot import CrowdMetricsSnapshot
from app.models.decision import DecisionResultRow
from app.models.evidence import EvidencePackage
from app.models.video import VideoAsset
from app.pipeline.decision_result import DecisionOutcome
from app.pipeline.session_report import (
    EventSummary,
    RiskOverview,
    SessionReportResult,
    TimelineEntry,
)
from app.services import evidence_service, incident_service

_TREND_WINDOW_SNAPSHOTS = 5
# UNVALIDATED ENGINEERING JUDGMENT: below this magnitude of risk_score
# change across the trend window, the trend is reported as STABLE rather
# than a noisy RISING/FALLING flip-flop on single-point fluctuation — not
# calibrated against real operator feedback on what magnitude "feels"
# meaningful, an honest starting point only.
_TREND_STABLE_EPSILON = 2.0

_STATUS_OBSERVED = "OBSERVED"
_STATUS_WATCH = "WATCH"
_STATUS_ABSTAINED = "ABSTAINED"
_STATUS_INCIDENT = "INCIDENT"

# Bounds how much of an event's own already-generated text is folded into
# the session-level behavioral/spatial aggregate — keeps the aggregate
# readable rather than a wall of concatenated paragraphs; never truncates
# the event's OWN full text (that remains available via the event list /
# incident drill-down).
_AGGREGATE_SNIPPET_MAX_LENGTH = 220


def _severity_tag(status: str, incident_priority: Optional[str]) -> str:
    if status == _STATUS_INCIDENT:
        return "CRITICAL" if incident_priority == "ELEVATED" else "HIGH"
    if status == _STATUS_WATCH:
        return "MODERATE"
    return "LOW"


def _location_description(roi_bbox: list, width: Optional[int], height: Optional[int]) -> str:
    if not width or not height or len(roi_bbox) != 4:
        return "unknown region"
    x_min, y_min, x_max, y_max = roi_bbox
    cx, cy = (x_min + x_max) / 2.0, (y_min + y_max) / 2.0
    col = "left" if cx < width / 3 else "right" if cx > 2 * width / 3 else "center"
    row = "upper" if cy < height / 3 else "lower" if cy > 2 * height / 3 else "middle"
    if row == "middle" and col == "center":
        return "center of frame"
    return f"{row}-{col} of frame"


def _snippet(text: Optional[str]) -> Optional[str]:
    if not text:
        return None
    text = text.strip()
    if len(text) <= _AGGREGATE_SNIPPET_MAX_LENGTH:
        return text
    return text[: _AGGREGATE_SNIPPET_MAX_LENGTH - 1].rstrip() + "…"


def _risk_overview(db: Session, session_id: uuid.UUID) -> RiskOverview:
    latest = (
        db.query(CrowdMetricsSnapshot)
        .filter(CrowdMetricsSnapshot.session_id == session_id)
        .order_by(CrowdMetricsSnapshot.timestamp_seconds.desc())
        .first()
    )
    if latest is None:
        return RiskOverview(
            current_state=None, current_score=None, trend="UNKNOWN", trend_delta=None,
            trend_window_seconds=None,
        )

    # Most recent N snapshots (descending, then reversed into chronological
    # order) — a dedicated small query rather than reusing
    # get_session_crowd_metrics_timeseries (which orders ASCENDING with a
    # head-limit and would return the session's EARLIEST rows under a small
    # limit, not the recent tail this trend calculation needs).
    recent_desc = (
        db.query(CrowdMetricsSnapshot)
        .filter(CrowdMetricsSnapshot.session_id == session_id)
        .order_by(CrowdMetricsSnapshot.timestamp_seconds.desc())
        .limit(_TREND_WINDOW_SNAPSHOTS)
        .all()
    )
    recent = list(reversed(recent_desc))

    if len(recent) < 2:
        trend, trend_delta, trend_window_seconds = "UNKNOWN", None, None
    else:
        oldest, newest = recent[0], recent[-1]
        delta = newest.risk_score - oldest.risk_score
        trend_window_seconds = newest.timestamp_seconds - oldest.timestamp_seconds
        if abs(delta) < _TREND_STABLE_EPSILON:
            trend = "STABLE"
        else:
            trend = "RISING" if delta > 0 else "FALLING"
        trend_delta = delta

    contributors = []
    if latest.congested_cell_fraction and latest.congested_cell_fraction > 0:
        contributors.append("congestion")
    if latest.reverse_flow_cell_fraction and latest.reverse_flow_cell_fraction > 0:
        contributors.append("reverse_flow")
    if latest.bottleneck_signal_present:
        contributors.append("bottleneck")

    total_snapshots = (
        db.query(CrowdMetricsSnapshot).filter(CrowdMetricsSnapshot.session_id == session_id).count()
    )

    return RiskOverview(
        current_state=latest.risk_state.value,
        current_score=latest.risk_score,
        trend=trend,
        trend_delta=trend_delta,
        trend_window_seconds=trend_window_seconds,
        primary_contributors=contributors,
        latest_snapshot_timestamp=latest.timestamp_seconds,
        snapshot_count=total_snapshots,
    )


def _event_status_and_outcome(
    latest_decision: Optional[DecisionResultRow],
) -> tuple[str, Optional[str]]:
    if latest_decision is None:
        return _STATUS_OBSERVED, None
    outcome = latest_decision.outcome
    if outcome == DecisionOutcome.ABSTAIN:
        return _STATUS_ABSTAINED, outcome.value
    if outcome == DecisionOutcome.WATCH:
        return _STATUS_WATCH, outcome.value
    if outcome == DecisionOutcome.INCIDENT:
        return _STATUS_INCIDENT, outcome.value
    return _STATUS_OBSERVED, outcome.value  # NO_INCIDENT


def _build_events(
    db: Session, session_id: uuid.UUID, video: Optional[VideoAsset],
) -> list[EventSummary]:
    packages_with_items = evidence_service.get_session_evidence_packages(db, session_id)
    # Ascending by timestamp for a readable report (get_session_evidence_
    # packages itself orders by created_at DESC, the right default for a
    # "most recent first" evidence feed but not for a chronological report).
    packages_with_items = sorted(packages_with_items, key=lambda pwi: pwi.package.timestamp_seconds)

    # incident_id per decision: derive by scanning IncidentEvidence links
    # for each incident once, rather than one query per event.
    incidents = incident_service.get_session_incidents(db, session_id)
    decision_id_to_incident_id: dict[uuid.UUID, uuid.UUID] = {}
    for incident in incidents:
        detail = incident_service.get_incident_detail(db, incident.id)
        if detail is None:
            continue
        for link in detail.linked_evidence:
            decision_id_to_incident_id[link.decision_result_id] = incident.id

    events: list[EventSummary] = []
    for pwi in packages_with_items:
        pkg = pwi.package
        decisions = (
            db.query(DecisionResultRow)
            .filter(DecisionResultRow.evidence_package_id == pkg.id)
            .order_by(DecisionResultRow.created_at.desc())
            .all()
        )
        # The LATEST decision naturally reflects a Verifier-driven
        # supersession (a brand-new, later-created ABSTAIN row) over the
        # original INCIDENT row — same "latest wins" semantics already used
        # by incident_service.is_decision_incident_worthy.
        latest_decision = decisions[0] if decisions else None
        status, decision_outcome = _event_status_and_outcome(latest_decision)

        onset_seconds = None
        duration_seconds = None
        if pkg.event_window:
            onset_seconds = pkg.event_window.get("onset_window_start_seconds")
            if onset_seconds is not None:
                duration_seconds = max(0.0, pkg.timestamp_seconds - onset_seconds)

        description = pkg.trigger_reason
        recommendation = None
        abstention_reason = None
        event_classification = None
        incident_id = None
        if latest_decision is not None:
            abstention_reason = latest_decision.abstention_reason
            if latest_decision.event_classification is not None:
                event_classification = latest_decision.event_classification.value
            if latest_decision.recommendation is not None:
                recommendation = latest_decision.recommendation.value
            if latest_decision.structured_report and latest_decision.structured_report.get("event_summary"):
                description = latest_decision.structured_report["event_summary"]
            elif latest_decision.reasoning_summary:
                description = latest_decision.reasoning_summary
            incident_id = decision_id_to_incident_id.get(latest_decision.id)

        incident_priority = None
        if incident_id is not None:
            matching_incident = next((i for i in incidents if i.id == incident_id), None)
            if matching_incident is not None:
                incident_priority = matching_incident.priority.value

        video_width = video.width if video else None
        video_height = video.height if video else None

        events.append(
            EventSummary(
                evidence_package_id=pkg.id,
                frame_number=pkg.frame_number,
                timestamp_seconds=pkg.timestamp_seconds,
                trigger_type=pkg.trigger_type.value,
                trigger_reason=pkg.trigger_reason,
                status=status,
                decision_outcome=decision_outcome,
                event_classification=event_classification,
                onset_seconds=onset_seconds,
                peak_seconds=pkg.timestamp_seconds,
                duration_seconds=duration_seconds,
                severity_tag=_severity_tag(status, incident_priority),
                confidence=pkg.confidence,
                location_description=_location_description(pkg.roi_bbox, video_width, video_height),
                description=description,
                observation_categories=sorted({item.category.value for item in pwi.items}),
                abstention_reason=abstention_reason,
                recommendation=recommendation,
                incident_id=incident_id,
            )
        )
    return events


def _build_timeline(
    db: Session, session_id: uuid.UUID, events: list[EventSummary],
) -> list[TimelineEntry]:
    from app.services import risk_state_service  # local import: avoids a module-level cycle risk with risk_state.py

    entries: list[TimelineEntry] = []
    risk_summary = risk_state_service.get_session_risk_summary(db, session_id)
    for transition in risk_summary.transition_history:
        entries.append(
            TimelineEntry(
                timestamp_seconds=transition.timestamp_seconds,
                kind="RISK_TRANSITION",
                description=(
                    f"Risk state changed {transition.previous_state.value} -> "
                    f"{transition.new_state.value} (risk_score={transition.risk_score_at_transition:.1f})"
                ),
            )
        )

    for event in events:
        entries.append(
            TimelineEntry(
                timestamp_seconds=event.timestamp_seconds,
                kind="EVENT",
                description=(
                    f"{event.trigger_type} trigger ({event.trigger_reason}) -> {event.status}"
                    + (f": {_snippet(event.description)}" if event.description else "")
                ),
                evidence_package_id=event.evidence_package_id,
            )
        )

    entries.sort(key=lambda e: e.timestamp_seconds)
    return entries


def _overview_summary(risk_overview: RiskOverview, investigated: int, confirmed: int) -> str:
    if risk_overview.current_state is None:
        risk_phrase = "No risk data has been recorded for this session yet."
    else:
        trend_phrase = {
            "RISING": "and has been rising",
            "FALLING": "and has been falling",
            "STABLE": "and has been stable",
            "UNKNOWN": "",
        }[risk_overview.trend]
        if risk_overview.trend_delta is not None and risk_overview.trend_window_seconds:
            trend_detail = (
                f" (Δ{risk_overview.trend_delta:+.1f} over the last "
                f"{risk_overview.trend_window_seconds:.1f}s)"
            )
        else:
            trend_detail = ""
        contributors_phrase = (
            f", driven primarily by {', '.join(risk_overview.primary_contributors)}"
            if risk_overview.primary_contributors else ""
        )
        risk_phrase = (
            f"Risk is currently {risk_overview.current_state} "
            f"({risk_overview.current_score:.1f}/100) {trend_phrase}{trend_detail}"
            f"{contributors_phrase}."
        )

    event_phrase = (
        f"{investigated} event(s) were investigated during this session; "
        f"{confirmed} were confirmed as incidents."
        if investigated > 0
        else "No events were investigated during this session."
    )
    return f"{risk_phrase} {event_phrase}".strip()


def _behavioral_analysis(events: list[EventSummary]) -> str:
    if not events:
        return "No behavioral analysis is available: no events were investigated during this session."
    lines = []
    for event in events:
        snippet = _snippet(event.description)
        if snippet:
            lines.append(f"[t={event.timestamp_seconds:.2f}s] ({event.status}) {snippet}")
    if not lines:
        return (
            f"{len(events)} event(s) were investigated, but none produced a "
            "semantic description (evidence incomplete or LLM unavailable)."
        )
    return " ".join(lines)


def _spatial_analysis(events: list[EventSummary]) -> str:
    if not events:
        return "No spatial analysis is available: no events were investigated during this session."
    region_counts: dict[str, int] = {}
    for event in events:
        region_counts[event.location_description] = region_counts.get(event.location_description, 0) + 1
    dominant_region, dominant_count = max(region_counts.items(), key=lambda kv: kv[1])
    return (
        f"Investigated events were concentrated in the {dominant_region} "
        f"({dominant_count} of {len(events)} event(s)). See the Risk and "
        "Pressure heatmaps for the corresponding spatial fields at each "
        "event's timestamp."
    )


def build_session_report(db: Session, session_id: uuid.UUID) -> Optional[SessionReportResult]:
    session = db.get(AnalysisSession, session_id)
    if session is None:
        return None
    video = db.get(VideoAsset, session.video_id)

    risk_overview = _risk_overview(db, session_id)
    events = _build_events(db, session_id, video)
    timeline = _build_timeline(db, session_id, events)
    incidents = incident_service.get_session_incidents(db, session_id)

    top_recommendation = None
    for event in events:
        if event.status == _STATUS_INCIDENT and event.recommendation:
            top_recommendation = event.recommendation
            break
    if top_recommendation is None:
        for event in events:
            if event.status == _STATUS_WATCH and event.recommendation:
                top_recommendation = event.recommendation
                break

    if incidents:
        incidents_summary = (
            f"{len(incidents)} confirmed incident(s) for this session."
        )
    elif events:
        counts: dict[str, int] = {}
        for event in events:
            counts[event.status] = counts.get(event.status, 0) + 1
        breakdown = ", ".join(f"{v} {k}" for k, v in counts.items())
        incidents_summary = (
            "No event met the evidence threshold required for incident "
            f"escalation. {len(events)} event(s) were investigated ({breakdown})."
        )
    else:
        incidents_summary = "No events were investigated during this session."

    return SessionReportResult(
        session_id=session_id,
        session_status=session.status.value,
        video_filename=video.original_filename if video else "unknown",
        video_duration_seconds=video.duration_seconds if video else None,
        risk_overview=risk_overview,
        investigated_event_count=len(events),
        confirmed_incident_count=len(incidents),
        events=events,
        timeline=timeline,
        overview_summary=_overview_summary(risk_overview, len(events), len(incidents)),
        incidents_summary=incidents_summary,
        behavioral_analysis=_behavioral_analysis(events),
        spatial_analysis=_spatial_analysis(events),
        top_recommendation=top_recommendation,
    )
