"""Phase 18, Step 7: should_verify() — True only for outcome==INCIDENT."""

import uuid

from app.pipeline.decision_result import DecisionOutcome, DecisionResult, RecommendationType
from app.pipeline.verification_gate import should_verify


def _decision(outcome: DecisionOutcome) -> DecisionResult:
    if outcome in (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH):
        return DecisionResult(
            decision_id=uuid.uuid4(), evidence_package_id=uuid.uuid4(),
            evidence_cited=["risk_score"], outcome=outcome,
            reasoning_summary="test", recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
            recommendation_rationale="test", projection_narrative=None,
            abstention_reason=None, confidence=0.8, binding_constraint="density_estimation_confidence",
        )
    if outcome == DecisionOutcome.ABSTAIN:
        return DecisionResult(
            decision_id=uuid.uuid4(), evidence_package_id=uuid.uuid4(),
            evidence_cited=[], outcome=outcome,
            reasoning_summary="test", recommendation=None,
            recommendation_rationale=None, projection_narrative=None,
            abstention_reason="test reason", confidence=0.3, binding_constraint="density_estimation_confidence",
        )
    return DecisionResult(
        decision_id=uuid.uuid4(), evidence_package_id=uuid.uuid4(),
        evidence_cited=["risk_score"], outcome=outcome,
        reasoning_summary="test", recommendation=None,
        recommendation_rationale=None, projection_narrative=None,
        abstention_reason=None, confidence=0.8, binding_constraint="density_estimation_confidence",
    )


def test_incident_is_verified():
    assert should_verify(_decision(DecisionOutcome.INCIDENT)) is True


def test_watch_is_not_verified():
    assert should_verify(_decision(DecisionOutcome.WATCH)) is False


def test_no_incident_is_not_verified():
    assert should_verify(_decision(DecisionOutcome.NO_INCIDENT)) is False


def test_abstain_is_not_verified():
    assert should_verify(_decision(DecisionOutcome.ABSTAIN)) is False
