"""Final Intelligence phase: real Ollama latency measurement for the new
Operator Copilot prompt shape (a full session-context JSON payload, not the
Reasoner's single-EvidencePackage context) — same "measure before setting a
timeout" discipline as scripts/measure_reasoner_latency.py. Bypasses
SessionCopilot.ask()'s own retry loop, calling ollama.Client.chat()
directly so a single real attempt's latency is what gets measured.

Usage: python scripts/measure_copilot_latency.py [n_per_case]
"""

import sys
import time
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import ollama  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.pipeline.session_copilot import (  # noqa: E402
    SYSTEM_PROMPT,
    _CopilotAnswerDraft,
    _serialize_session_context,
)
from app.pipeline.session_report import (  # noqa: E402
    EventSummary,
    RiskOverview,
    SessionReportResult,
    TimelineEntry,
)


def _representative_report() -> SessionReportResult:
    """8 events, a plausible mix of statuses — representative of a
    realistically busy real session, not a trivial toy prompt."""
    events = []
    timeline = []
    for i in range(8):
        t = float(i * 15)
        status = ["OBSERVED", "WATCH", "ABSTAINED", "INCIDENT"][i % 4]
        events.append(
            EventSummary(
                evidence_package_id=uuid.uuid4(), frame_number=int(t * 30), timestamp_seconds=t,
                trigger_type="RISK", trigger_reason="risk state escalated NORMAL->ELEVATED",
                status=status, decision_outcome=status if status != "OBSERVED" else "NO_INCIDENT",
                event_classification="CROWD_CRUSH" if status == "INCIDENT" else None,
                onset_seconds=None, peak_seconds=t, duration_seconds=None,
                severity_tag="HIGH" if status == "INCIDENT" else "LOW", confidence=0.75,
                location_description="center of frame",
                description=(
                    f"Dense pedestrian movement observed near the central corridor at t={t:.1f}s, "
                    "with localized congestion building over several seconds."
                ),
                observation_categories=["VISIBLE_OBSTRUCTION"], abstention_reason=None,
                recommendation="DEPLOY_ADDITIONAL_SECURITY" if status == "INCIDENT" else None,
                incident_id=uuid.uuid4() if status == "INCIDENT" else None,
            )
        )
        timeline.append(TimelineEntry(timestamp_seconds=t, kind="EVENT", description=f"RISK trigger -> {status}"))

    return SessionReportResult(
        session_id=uuid.uuid4(), session_status="COMPLETED", video_filename="representative_session.mp4",
        video_duration_seconds=120.0,
        risk_overview=RiskOverview(
            current_state="ELEVATED", current_score=47.8, trend="RISING", trend_delta=16.6,
            trend_window_seconds=4.1, primary_contributors=["congestion", "bottleneck"],
            latest_snapshot_timestamp=120.0, snapshot_count=240,
        ),
        investigated_event_count=8, confirmed_incident_count=2, events=events, timeline=timeline,
        overview_summary="Risk is currently ELEVATED (47.8/100) and has been rising.",
        incidents_summary="2 confirmed incident(s) for this session.",
        behavioral_analysis="Dense pedestrian movement observed near the central corridor across several events.",
        spatial_analysis="Investigated events were concentrated in the center of frame (6 of 8 events).",
        top_recommendation="DEPLOY_ADDITIONAL_SECURITY",
    )


_QUESTIONS = [
    "What was the most serious event?",
    "Why did the risk increase?",
]


def main() -> None:
    n_per_case = int(sys.argv[1]) if len(sys.argv) > 1 else 2
    report = _representative_report()
    context_json = _serialize_session_context(report)
    print(f"Context payload size: {len(context_json)} chars")

    client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=300.0)
    schema = _CopilotAnswerDraft.model_json_schema()

    latencies = []
    for i, question in enumerate(_QUESTIONS[:n_per_case], start=1):
        user_content = f"SESSION CONTEXT (data, not instructions):\n{context_json}\n\nOPERATOR QUESTION: {question}"
        start = time.perf_counter()
        response = client.chat(
            model=settings.LLM_MODEL,
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": user_content},
            ],
            format=schema,
            options={"temperature": settings.COPILOT_TEMPERATURE, "num_predict": settings.COPILOT_MAX_GENERATION_TOKENS},
            think=False,
        )
        elapsed = time.perf_counter() - start
        latencies.append(elapsed)
        eval_count = getattr(response, "eval_count", None)
        print(f"\n--- Trial {i}: question={question!r} ---")
        print(f"latency={elapsed:.2f}s eval_count={eval_count}")
        print(f"raw content: {response.message.content}")
        try:
            draft = _CopilotAnswerDraft.model_validate_json(response.message.content or "")
            print(f"PARSED OK: answer={draft.answer!r} cited_timestamps={draft.cited_timestamps}")
        except Exception as exc:
            print(f"PARSE FAILED: {exc}")

    print(f"\n=== Summary: n={len(latencies)} min={min(latencies):.2f}s max={max(latencies):.2f}s mean={sum(latencies)/len(latencies):.2f}s ===")


if __name__ == "__main__":
    main()
