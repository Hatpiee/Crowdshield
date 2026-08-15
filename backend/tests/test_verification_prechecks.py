"""Phase 18, Step 7: deterministic pre-checks — each independently correct
on both passing and failing synthetic inputs."""

import uuid

from app.pipeline.decision_result import DecisionOutcome, DecisionResult, RecommendationType
from app.pipeline.evidence_package import EvidencePackageResult, RiskStateSnapshot
from app.pipeline.risk_state import RiskState
from app.pipeline.trigger_engine import TriggerType
from app.pipeline.verification_prechecks import (
    check_confidence_consistency,
    check_evidence_grounding_existence,
)
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary,
    EvidenceType,
    NormalizedBoundingBox,
    ObservationCategory,
    VisionObservation,
)

REAL_OBSERVATION_ID = uuid.uuid4()


def _package(confidence: float = 0.8) -> EvidencePackageResult:
    return EvidencePackageResult(
        package_id=uuid.uuid4(),
        schema_version="1.1",
        session_id=uuid.uuid4(),
        frame_number=0,
        timestamp_seconds=0.0,
        trigger_type=TriggerType.RISK,
        trigger_reason="test",
        model_config_id=uuid.uuid4(),
        crowd_metrics_summary=CompactCrowdMetricsSummary(
            risk_score=90.0, risk_state=RiskState.CRITICAL, max_density=0.2, max_pressure=15.0,
            pressure_units_disclaimer="test", congested_cell_fraction=0.1,
            reverse_flow_cell_fraction=0.0, bottleneck_signal_present=True, density_confidence=confidence,
        ),
        risk_state_snapshot=RiskStateSnapshot(
            frame_number=0, timestamp_seconds=0.0, state=RiskState.CRITICAL.value, risk_score=90.0,
        ),
        vision_observations=[
            VisionObservation(
                observation_id=REAL_OBSERVATION_ID,
                category=ObservationCategory.BOTTLENECK,
                description="test observation",
                region=NormalizedBoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=1.0),
                confidence=0.8,
                evidence_type=EvidenceType.INFERRED,
            )
        ],
        vlm_call_succeeded=True,
        confidence=confidence,
        binding_constraint="density_estimation_confidence",
        complete=True,
        missing=[],
        contradictions=[],
    )


def _decision(evidence_cited: list[str], confidence: float = 0.8) -> DecisionResult:
    return DecisionResult(
        decision_id=uuid.uuid4(), evidence_package_id=uuid.uuid4(),
        evidence_cited=evidence_cited, outcome=DecisionOutcome.INCIDENT,
        reasoning_summary="test", recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
        recommendation_rationale="test", projection_narrative=None,
        abstention_reason=None, confidence=confidence, binding_constraint="density_estimation_confidence",
    )


def test_confidence_consistency_passes_when_equal():
    package = _package(confidence=0.8)
    decision = _decision(evidence_cited=["risk_score"], confidence=0.8)
    assert check_confidence_consistency(decision, package) is True


def test_confidence_consistency_fails_when_different():
    package = _package(confidence=0.8)
    decision = _decision(evidence_cited=["risk_score"], confidence=0.7)
    assert check_confidence_consistency(decision, package) is False


def test_evidence_grounding_passes_for_valid_field_name_and_observation_id():
    package = _package()
    decision = _decision(evidence_cited=["risk_score", "bottleneck_signal_present", str(REAL_OBSERVATION_ID)])
    assert check_evidence_grounding_existence(decision, package) is True


def test_evidence_grounding_fails_for_fabricated_citation():
    package = _package()
    decision = _decision(evidence_cited=["risk_score", "this_field_does_not_exist_anywhere"])
    assert check_evidence_grounding_existence(decision, package) is False


def test_evidence_grounding_fails_for_fabricated_observation_id():
    package = _package()
    decision = _decision(evidence_cited=[str(uuid.uuid4())])  # a real UUID, but NOT the real one above
    assert check_evidence_grounding_existence(decision, package) is False
