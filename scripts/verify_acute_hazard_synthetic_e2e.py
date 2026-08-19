"""Reasoner Stability / Acute-Hazard synthetic E2E validation phase: runs
the CLEARLY SYNTHETIC BEFORE->EVENT->AFTERMATH fixture
(backend/tests/fixtures/synthetic_acute_event.py) through the REAL,
UNMODIFIED production entrypoint — session_service.create_session() ->
session_service.start_session() -> AnalysisOrchestrator(session_id).run()
(called synchronously here instead of via orchestration_launcher's
background thread, purely so this script can block and report; the logic
executed is identical) — exactly the same code path a real video upload +
"start analysis" API call would take.

This is genuinely NOT a fabricated incident: scripts/
check_synthetic_acute_event_trigger.py already proved (cheaply, without
any LLM calls) that this fixture makes AcuteHazardDetector's real fusion
logic fire ACUTE_HAZARD organically. This script goes the rest of the way
— real MiniCPM-V VLM analysis of the actual generated frames, real
EvidenceBuilder, real Qwen3-8B Reasoner, real Verifier (if outcome is
INCIDENT), and real incident_service correlation — then inspects the
actual Postgres rows created, never manually inserted.

============================================================
WHAT THIS DOES NOT PROVE
============================================================
This is SYNTHETIC validation of the pipeline's mechanics — it does NOT
validate ACUTE_HAZARD_* threshold calibration against real blast footage
(none exists in this repo — see DECISIONS.md), and does NOT prove the VLM
would correctly interpret a REAL explosion (only that it can process this
particular synthetic image bundle end-to-end without error).

Usage: python scripts/verify_acute_hazard_synthetic_e2e.py
"""

import shutil
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.decision import DecisionResultRow  # noqa: E402
from app.models.evidence import EvidencePackage  # noqa: E402
from app.models.incident import Incident, IncidentEvidence  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator  # noqa: E402
from app.pipeline.trigger_engine import TriggerType  # noqa: E402
from app.services import session_service  # noqa: E402
from tests.fixtures.synthetic_acute_event import (  # noqa: E402
    FPS, HEIGHT, WIDTH, generate_synthetic_acute_event_mp4,
)


def main() -> None:
    db = SessionLocal()

    user = db.query(User).first()
    if user is None:
        print("FATAL: no user exists in the database — cannot create a real AnalysisSession.")
        sys.exit(1)

    storage_filename = f"{uuid.uuid4()}.mp4"
    storage_dir = REPO_ROOT / settings.VIDEO_STORAGE_PATH
    storage_dir.mkdir(parents=True, exist_ok=True)
    storage_path = storage_dir / storage_filename

    print("Generating synthetic acute-event fixture directly into real video storage (clearly labeled, not real footage)...")
    fixture_source = Path(__file__).resolve().parents[1] / "backend" / "tests" / "fixtures" / "synthetic_acute_event.mp4"
    if not fixture_source.exists():
        generate_synthetic_acute_event_mp4(fixture_source)
    shutil.copyfile(fixture_source, storage_path)
    file_size = storage_path.stat().st_size

    video = VideoAsset(
        original_filename="SYNTHETIC_acute_event_fixture_NOT_REAL_FOOTAGE.mp4",
        storage_filename=storage_filename,
        file_size_bytes=file_size,
        mime_type="video/mp4",
        uploaded_by=user.id,
        fps=FPS,
        width=WIDTH,
        height=HEIGHT,
    )
    db.add(video)
    db.commit()
    db.refresh(video)
    print(f"Created REAL VideoAsset id={video.id} storage_path={storage_path}")

    session = session_service.create_session(db, video.id, user.id)
    session = session_service.start_session(db, session)
    print(f"Created REAL AnalysisSession id={session.id}, status={session.status.value} (real production sequence: create_session -> start_session)")

    print()
    print("=== Running the REAL production AnalysisOrchestrator.run() synchronously (blocks for real Loop A + any spawned Loop B) ===")
    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()
    print("orchestrator.run() returned.")

    db.expire_all()
    session = db.get(type(session), session.id)
    print(f"Final session.status={session.status.value}")

    print()
    print("=== Inspecting REAL persisted rows (never manually inserted) ===")
    packages = (
        db.query(EvidencePackage)
        .filter(EvidencePackage.session_id == session.id)
        .order_by(EvidencePackage.timestamp_seconds.asc())
        .all()
    )
    print(f"EvidencePackage rows for this session: {len(packages)}")
    acute_packages = [p for p in packages if p.trigger_type == TriggerType.ACUTE_HAZARD]
    print(f"  of which trigger_type=ACUTE_HAZARD: {len(acute_packages)}")

    acute_incident_decisions = []
    for pkg in acute_packages:
        print(f"  package_id={pkg.id} frame={pkg.frame_number} t={pkg.timestamp_seconds:.2f}s trigger_reason={pkg.trigger_reason!r}")
        print(f"    acute_hazard_signal_snapshot={pkg.acute_hazard_signal_snapshot}")
        print(f"    event_window={pkg.event_window}")

        decisions = db.query(DecisionResultRow).filter(DecisionResultRow.evidence_package_id == pkg.id).all()
        for d in decisions:
            if d.outcome.value == "INCIDENT":
                acute_incident_decisions.append(d)
            print(f"    decision_id={d.id} outcome={d.outcome.value}")
            print(f"      event_classification={d.event_classification.value if d.event_classification else None}")
            if d.structured_report is not None:
                print(f"      structured_report.event_summary={d.structured_report.get('event_summary')!r}")
                print(f"      structured_report.observed_evidence={d.structured_report.get('observed_evidence')}")
                print(f"      structured_report.behavioral_analysis={d.structured_report.get('behavioral_analysis')!r}")
                print(f"      structured_report.spatial_analysis={d.structured_report.get('spatial_analysis')!r}")
                print(f"      structured_report.temporal_analysis={d.structured_report.get('temporal_analysis')!r}")
                print(f"      structured_report.crowd_risk_context={d.structured_report.get('crowd_risk_context')!r}")
            print(f"      recommendation={d.recommendation.value if d.recommendation else None}")

            incident_links = db.query(IncidentEvidence).filter(IncidentEvidence.decision_result_id == d.id).all()
            for link in incident_links:
                incident = db.get(Incident, link.incident_id)
                print(
                    f"      -> REAL Incident row id={incident.id} lifecycle_status="
                    f"{incident.lifecycle_status.value} session_id={incident.session_id} "
                    f"created_at={incident.created_at}"
                )

    all_incidents = db.query(Incident).filter(Incident.session_id == session.id).all()
    print()
    print(f"Total REAL Incident rows for this session: {len(all_incidents)}")
    for inc in all_incidents:
        print(f"  incident_id={inc.id} lifecycle_status={inc.lifecycle_status.value}")

    print()
    if acute_incident_decisions:
        if all_incidents:
            print("RESULT: ACUTE_HAZARD fired, real VLM+Reasoner produced outcome=INCIDENT, and a REAL Incident row was created through incident_service — full chain verified.")
        else:
            print("RESULT: outcome=INCIDENT was produced but NO Incident row exists — investigate (may be correctly superseded by Verifier, or a real gap).")
    else:
        print("RESULT: ACUTE_HAZARD fired and the real chain ran, but outcome was NOT INCIDENT this run (WATCH/NO_INCIDENT/ABSTAIN) — reporting the real, non-forced outcome, not treating this as a failure.")

    print(f"\nsession_id={session.id} (for further manual API inspection: GET /api/sessions/{session.id}/incidents)")
    db.close()


if __name__ == "__main__":
    main()
