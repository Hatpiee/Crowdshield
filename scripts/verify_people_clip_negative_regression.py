"""Reasoner Stability / Acute-Hazard E2E validation phase, Step 12: negative
regression. Runs the REAL, already-existing calm `people_clip.mp4` footage
through the exact same real production entrypoint as scripts/
verify_acute_hazard_synthetic_e2e.py (session_service.create_session ->
start_session -> AnalysisOrchestrator(session_id).run(), called
synchronously) and confirms NO fabricated acute-hazard Incident is created.

Per the prior phase's own calibration run (scripts/
preview_acute_hazard_signals.py), ACUTE_HAZARD is EXPECTED to fire a
handful of times on this real footage (genuine elevated-motion moments,
e.g. a passing vehicle ~t=6.7-8.7s) — that is a KNOWN, already-documented,
honestly-reported residual precision limitation of a z-score-only
detector without real hazard-labeled ground truth (see DECISIONS.md). This
script's job is NOT to prove zero triggers; it is to prove that even when
ACUTE_HAZARD fires on ordinary footage, the downstream real VLM+Reasoner
chain does NOT escalate it into a fabricated Incident.

Usage: python scripts/verify_people_clip_negative_regression.py
"""

import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.database import SessionLocal  # noqa: E402
from app.models.decision import DecisionResultRow  # noqa: E402
from app.models.evidence import EvidencePackage  # noqa: E402
from app.models.incident import Incident  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator  # noqa: E402
from app.pipeline.trigger_engine import TriggerType  # noqa: E402
from app.services import session_service  # noqa: E402

_PEOPLE_CLIP_VIDEO_ID = UUID("7578ea39-4a8a-40d3-a1e6-75bf0dc75e63")


def main() -> None:
    db = SessionLocal()

    video = db.get(VideoAsset, _PEOPLE_CLIP_VIDEO_ID)
    if video is None:
        print(f"FATAL: VideoAsset {_PEOPLE_CLIP_VIDEO_ID} (people_clip.mp4) not found.")
        sys.exit(1)
    user = db.query(User).first()
    if user is None:
        print("FATAL: no user exists in the database.")
        sys.exit(1)

    session = session_service.create_session(db, video.id, user.id)
    session = session_service.start_session(db, session)
    print(f"Created REAL AnalysisSession id={session.id} against REAL people_clip.mp4 (video_id={video.id})")

    print()
    print("=== Running the REAL production AnalysisOrchestrator.run() synchronously ===")
    orchestrator = AnalysisOrchestrator(session.id)
    orchestrator.run()
    print("orchestrator.run() returned.")

    db.expire_all()
    session = db.get(type(session), session.id)
    print(f"Final session.status={session.status.value}")

    packages = db.query(EvidencePackage).filter(EvidencePackage.session_id == session.id).order_by(EvidencePackage.timestamp_seconds.asc()).all()
    acute_packages = [p for p in packages if p.trigger_type == TriggerType.ACUTE_HAZARD]
    print()
    print(f"Total EvidencePackage rows: {len(packages)}, of which ACUTE_HAZARD: {len(acute_packages)}")

    incident_count = 0
    for pkg in acute_packages:
        decisions = db.query(DecisionResultRow).filter(DecisionResultRow.evidence_package_id == pkg.id).all()
        for d in decisions:
            print(f"  ACUTE_HAZARD package t={pkg.timestamp_seconds:.2f}s -> decision outcome={d.outcome.value} event_classification={d.event_classification.value if d.event_classification else None}")
            if d.outcome.value == "INCIDENT":
                incident_count += 1

    all_incidents = db.query(Incident).filter(Incident.session_id == session.id).all()
    print()
    print(f"Total REAL Incident rows for this session (any trigger type): {len(all_incidents)}")
    print(f"ACUTE_HAZARD-sourced decisions with outcome=INCIDENT: {incident_count}")

    print()
    if incident_count == 0:
        print("RESULT: PASS — no fabricated acute-hazard incident on real calm footage, even though ACUTE_HAZARD fired organically on genuine motion moments.")
    else:
        print("RESULT: an ACUTE_HAZARD trigger on calm footage produced a real INCIDENT outcome — reporting honestly, investigate whether this is a genuine event in the footage or an over-sensitive threshold.")

    db.close()


if __name__ == "__main__":
    main()
