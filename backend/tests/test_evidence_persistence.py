"""Phase 16, Step 6: real-DB persistence tests for evidence_service.py."""

import inspect
import uuid

import numpy as np

from app.models.evidence import EvidenceItem, EvidencePackage
from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.frame import Frame
from app.pipeline.risk_state import RiskState
from app.services import evidence_service, session_service

from tests.test_evidence_builder import (
    _crowd_metrics,
    _observation,
    _risk_state_result,
    _trigger_decision,
    _vision_result,
)

FRAME_WIDTH = 64
FRAME_HEIGHT = 64


def _frame() -> Frame:
    image = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), 128, dtype=np.uint8)
    return Frame(frame_number=0, timestamp_seconds=0.0, image=image, width=FRAME_WIDTH, height=FRAME_HEIGHT)


def _build_result(db_session, session, observations=None):
    observations = observations if observations is not None else [_observation()]
    return EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result(observations),
        vlm_call_succeeded=True,
    )


def test_persist_creates_package_and_item_rows(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    observations = [_observation(), _observation()]
    result = _build_result(db_session, session, observations=observations)

    package = evidence_service.persist_evidence_package(db_session, result)

    package_rows = db_session.query(EvidencePackage).filter(EvidencePackage.id == package.id).all()
    assert len(package_rows) == 1

    item_rows = (
        db_session.query(EvidenceItem)
        .filter(EvidenceItem.evidence_package_id == package.id)
        .all()
    )
    assert len(item_rows) == 2
    assert package_rows[0].session_id == session.id
    assert package_rows[0].complete == result.complete
    assert package_rows[0].confidence == result.confidence


def test_jsonb_fields_round_trip_intact_including_nested_units_disclaimer(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    result = _build_result(db_session, session)
    package = evidence_service.persist_evidence_package(db_session, result)

    db_session.expire_all()
    reloaded = db_session.get(EvidencePackage, package.id)

    assert reloaded.crowd_metrics_summary == result.crowd_metrics_summary.model_dump(mode="json")
    disclaimer = reloaded.crowd_metrics_summary["pressure_units_disclaimer"]
    assert disclaimer == result.crowd_metrics_summary.pressure_units_disclaimer
    assert disclaimer  # non-empty: not silently lost/truncated two levels deep
    assert reloaded.risk_state_snapshot["state"] == result.risk_state_snapshot.state
    assert reloaded.missing_evidence == result.missing
    assert reloaded.contradictions == [
        {
            "contradiction_type": c.contradiction_type,
            "description": c.description,
            "resolution_status": c.resolution_status,
        }
        for c in result.contradictions
    ]


def test_no_update_function_exists_for_persisted_rows():
    # RESOLUTION 4: immutability is enforced by the ABSENCE of any
    # update/modify code path in this module — a source-level check, not
    # just a docstring assertion (same spirit as Phase 8's AST-based
    # independence test in test_dis_optical_flow.py).
    functions = [
        name
        for name, obj in inspect.getmembers(evidence_service, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == evidence_service.__name__
    ]
    forbidden_substrings = ("update", "modify", "edit", "patch", "set_")
    offending = [
        name for name in functions
        if any(substring in name.lower() for substring in forbidden_substrings)
    ]
    assert offending == [], f"Found forbidden update-like function(s): {offending}"
    assert set(functions) == {
        "persist_evidence_package",
        "get_session_evidence_packages",
        "get_evidence_package",
    }


def test_get_session_evidence_packages_ordering_most_recent_first(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    first = evidence_service.persist_evidence_package(db_session, _build_result(db_session, session))
    second = evidence_service.persist_evidence_package(db_session, _build_result(db_session, session))

    entries = evidence_service.get_session_evidence_packages(db_session, session.id)
    assert [entry.package.id for entry in entries] == [second.id, first.id]
    assert len(entries[0].items) == 1


def test_get_evidence_package_found_and_not_found(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)

    package = evidence_service.persist_evidence_package(db_session, _build_result(db_session, session))

    entry = evidence_service.get_evidence_package(db_session, package.id)
    assert entry is not None
    assert entry.package.id == package.id
    assert len(entry.items) == 1

    assert evidence_service.get_evidence_package(db_session, uuid.uuid4()) is None
