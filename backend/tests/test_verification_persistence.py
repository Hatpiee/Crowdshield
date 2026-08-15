"""Phase 18, Step 7: verification_service.py persistence tests. Uses a
lightweight fake Verifier (duck-typed, matching Verifier.verify()'s exact
signature) to control passed/failed outcomes deterministically — real LLM
behavior is covered separately in test_verifier.py; these tests are about
PERSISTENCE correctness, not model judgment."""

import inspect
import uuid
from dataclasses import dataclass

from app.models.decision import DecisionResultRow
from app.models.verification import VerificationResultRow
from app.pipeline.decision_result import DecisionOutcome, DecisionResult, RecommendationType
from app.pipeline.verification_result import VerificationResult
from app.services import decision_service, evidence_service, session_service, verification_service

from tests.test_evidence_builder import (
    _crowd_metrics,
    _frame,
    _observation,
    _risk_state_result,
    _trigger_decision,
    _vision_result,
)
from app.pipeline.evidence_builder import EvidenceBuilder


@dataclass
class _FakeVerifier:
    result: VerificationResult

    def verify(self, decision, evidence_package):
        return self.result


def _setup(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    session = session_service.create_session(db_session, video.id, user.id)
    evidence_result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([_observation()]),
        vlm_call_succeeded=True,
    )
    package = evidence_service.persist_evidence_package(db_session, evidence_result)

    decision = DecisionResult(
        decision_id=uuid.uuid4(), evidence_package_id=package.id,
        evidence_cited=["risk_score"], outcome=DecisionOutcome.INCIDENT,
        reasoning_summary="original reasoning", recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
        recommendation_rationale="original rationale", projection_narrative=None,
        abstention_reason=None, confidence=evidence_result.confidence,
        binding_constraint=evidence_result.binding_constraint,
    )
    original_row = decision_service.persist_decision_result(db_session, decision)
    return evidence_result, decision, original_row


def test_passed_verification_creates_one_row_no_new_decision(db_session, make_video, test_user):
    evidence_result, decision, original_row = _setup(db_session, make_video, test_user)

    fake_result = VerificationResult(
        verification_id=uuid.uuid4(), decision_id=decision.decision_id, passed=True,
        confidence_consistency_ok=True, evidence_grounding_existence_ok=True,
        llm_checks=None, issues_found=[],
    )
    before_decision_count = db_session.query(DecisionResultRow).count()

    outcome = verification_service.run_verification_if_warranted(
        db_session, _FakeVerifier(fake_result), decision, evidence_result
    )

    assert outcome.applicable is True
    assert outcome.verification_result.passed is True
    assert outcome.superseding_decision is None

    verification_rows = (
        db_session.query(VerificationResultRow)
        .filter(VerificationResultRow.decision_id == decision.decision_id)
        .all()
    )
    assert len(verification_rows) == 1
    after_decision_count = db_session.query(DecisionResultRow).count()
    assert after_decision_count == before_decision_count


def test_failed_verification_creates_superseding_decision(db_session, make_video, test_user):
    evidence_result, decision, original_row = _setup(db_session, make_video, test_user)

    fake_result = VerificationResult(
        verification_id=uuid.uuid4(), decision_id=decision.decision_id, passed=False,
        confidence_consistency_ok=True, evidence_grounding_existence_ok=True,
        llm_checks=None, issues_found=["unsupported_claims: fabricated a number"],
    )

    outcome = verification_service.run_verification_if_warranted(
        db_session, _FakeVerifier(fake_result), decision, evidence_result
    )

    assert outcome.applicable is True
    assert outcome.verification_result.passed is False
    assert outcome.superseding_decision is not None
    assert outcome.superseding_decision.outcome == DecisionOutcome.ABSTAIN
    assert outcome.superseding_decision.superseded_decision_id == decision.decision_id

    verification_rows = (
        db_session.query(VerificationResultRow)
        .filter(VerificationResultRow.decision_id == decision.decision_id)
        .all()
    )
    assert len(verification_rows) == 1
    assert verification_rows[0].superseding_decision_id == outcome.superseding_decision.id


def test_original_decision_row_unchanged_after_failed_verification(db_session, make_video, test_user):
    evidence_result, decision, original_row = _setup(db_session, make_video, test_user)

    before_snapshot = {
        "id": original_row.id, "evidence_package_id": original_row.evidence_package_id,
        "evidence_cited": original_row.evidence_cited, "outcome": original_row.outcome,
        "reasoning_summary": original_row.reasoning_summary,
        "recommendation": original_row.recommendation,
        "recommendation_rationale": original_row.recommendation_rationale,
        "projection_narrative": original_row.projection_narrative,
        "abstention_reason": original_row.abstention_reason,
        "confidence": original_row.confidence, "binding_constraint": original_row.binding_constraint,
        "superseded_decision_id": original_row.superseded_decision_id,
        "created_at": original_row.created_at,
    }

    fake_result = VerificationResult(
        verification_id=uuid.uuid4(), decision_id=decision.decision_id, passed=False,
        confidence_consistency_ok=True, evidence_grounding_existence_ok=True,
        llm_checks=None, issues_found=["test issue"],
    )
    verification_service.run_verification_if_warranted(
        db_session, _FakeVerifier(fake_result), decision, evidence_result
    )

    db_session.expire_all()
    reloaded = db_session.get(DecisionResultRow, decision.decision_id)
    for field_name, before_value in before_snapshot.items():
        assert getattr(reloaded, field_name) == before_value, f"{field_name} changed!"


def test_no_update_function_exists_in_verification_service():
    functions = [
        name
        for name, obj in inspect.getmembers(verification_service, inspect.isfunction)
        if not name.startswith("_") and obj.__module__ == verification_service.__name__
    ]
    forbidden_substrings = ("update", "modify", "edit", "patch", "set_")
    offending = [
        name for name in functions
        if any(substring in name.lower() for substring in forbidden_substrings)
    ]
    assert offending == [], f"Found forbidden update-like function(s): {offending}"
    assert functions == ["run_verification_if_warranted"]
