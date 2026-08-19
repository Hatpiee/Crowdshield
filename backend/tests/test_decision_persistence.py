"""Phase 17, Step 7: real-DB persistence tests for decision_service.py,
mirroring Phase 16's test_evidence_persistence.py exact patterns."""

import inspect
import uuid

from app.models.decision import DecisionResultRow
from app.pipeline.decision_result import (
    DecisionOutcome,
    DecisionResult,
    EventClassification,
    RecommendationType,
)
from app.services import decision_service, evidence_service, session_service

from tests.test_evidence_builder import (
    _crowd_metrics,
    _frame,
    _observation,
    _risk_state_result,
    _structured_report,
    _trigger_decision,
    _vision_result,
)
from app.pipeline.evidence_builder import EvidenceBuilder


def _persisted_evidence_package(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([_observation()]),
        vlm_call_succeeded=True,
    )
    return evidence_service.persist_evidence_package(db_session, result), session


def _decision_result(evidence_package_id, outcome=DecisionOutcome.INCIDENT) -> DecisionResult:
    if outcome in (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH):
        return DecisionResult(
            decision_id=uuid.uuid4(), evidence_package_id=evidence_package_id,
            evidence_cited=["risk_score"], outcome=outcome,
            reasoning_summary="test reasoning", recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
            recommendation_rationale="test rationale", projection_narrative=None,
            event_classification=EventClassification.CROWD_CRUSH,
            structured_report=_structured_report() if outcome == DecisionOutcome.INCIDENT else None,
            abstention_reason=None, confidence=0.8, binding_constraint="density_estimation_confidence",
        )
    return DecisionResult(
        decision_id=uuid.uuid4(), evidence_package_id=evidence_package_id,
        evidence_cited=["risk_score"], outcome=outcome,
        reasoning_summary="test reasoning", recommendation=None,
        recommendation_rationale=None, projection_narrative=None,
        abstention_reason=None, confidence=0.8, binding_constraint="density_estimation_confidence",
    )


def test_persist_creates_decision_row(db_session, make_video, test_user):
    package, _ = _persisted_evidence_package(db_session, make_video, test_user)
    result = _decision_result(package.id)

    row = decision_service.persist_decision_result(db_session, result)

    rows = db_session.query(DecisionResultRow).filter(DecisionResultRow.id == row.id).all()
    assert len(rows) == 1
    assert rows[0].evidence_package_id == package.id
    assert rows[0].outcome == DecisionOutcome.INCIDENT
    assert rows[0].recommendation == RecommendationType.DEPLOY_ADDITIONAL_SECURITY
    assert rows[0].confidence == 0.8


def test_round_trip_correctness(db_session, make_video, test_user):
    package, _ = _persisted_evidence_package(db_session, make_video, test_user)
    result = _decision_result(package.id, outcome=DecisionOutcome.NO_INCIDENT)

    row = decision_service.persist_decision_result(db_session, result)

    db_session.expire_all()
    reloaded = db_session.get(DecisionResultRow, row.id)
    assert reloaded.evidence_cited == result.evidence_cited
    assert reloaded.outcome == DecisionOutcome.NO_INCIDENT
    assert reloaded.recommendation is None
    assert reloaded.recommendation_rationale is None


def test_no_update_function_exists_for_persisted_rows():
    functions = [
        name
        for name, obj in inspect.getmembers(decision_service, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == decision_service.__name__
    ]
    forbidden_substrings = ("update", "modify", "edit", "patch", "set_")
    offending = [
        name for name in functions
        if any(substring in name.lower() for substring in forbidden_substrings)
    ]
    assert offending == [], f"Found forbidden update-like function(s): {offending}"
    assert set(functions) == {
        "persist_decision_result",
        "get_session_decisions",
        "get_decision_result",
        # Phase 18 addition: a read-only helper, not an update path.
        "get_verification_for_decision",
    }


def test_get_session_decisions_ordering_and_scoping(db_session, make_video, test_user):
    package, session = _persisted_evidence_package(db_session, make_video, test_user)
    first = decision_service.persist_decision_result(db_session, _decision_result(package.id))
    second = decision_service.persist_decision_result(
        db_session, _decision_result(package.id, outcome=DecisionOutcome.NO_INCIDENT)
    )

    rows = decision_service.get_session_decisions(db_session, session.id)
    assert [row.id for row in rows] == [second.id, first.id]


def test_get_decision_result_found_and_not_found(db_session, make_video, test_user):
    package, _ = _persisted_evidence_package(db_session, make_video, test_user)
    row = decision_service.persist_decision_result(db_session, _decision_result(package.id))

    found = decision_service.get_decision_result(db_session, row.id)
    assert found is not None
    assert found.id == row.id

    assert decision_service.get_decision_result(db_session, uuid.uuid4()) is None
