"""Phase 17, Step 7: Reasoner tests. Includes REAL (non-mocked) inference
tests against the actually-pulled qwen3:8b tag — same "never claim tested
without testing" standard as Phase 14's test_minicpm_vlm.py."""

import re
import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.pipeline.decision_result import DecisionOutcome, DecisionResult, _LLMDecisionDraft
from app.pipeline.evidence_package import (
    Contradiction,
    EvidencePackageResult,
    PredictiveProjectionSnapshot,
    RiskStateSnapshot,
)
from app.pipeline.reasoner import LLMUnavailableError, Reasoner
from app.pipeline.risk_state import RiskState
from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary,
    EvidenceType,
    NormalizedBoundingBox,
    ObservationCategory,
    VisionObservation,
)


def _package(
    confidence: float = 0.9,
    contradictions: list[Contradiction] = None,
    complete: bool = True,
    missing: list[str] = None,
    risk_score: float = 20.0,
    risk_state: RiskState = RiskState.NORMAL,
    vision_observations: list[VisionObservation] = None,
    predictive_projection_snapshot: PredictiveProjectionSnapshot = None,
    trigger_reason: str = "test trigger",
) -> EvidencePackageResult:
    return EvidencePackageResult(
        package_id=uuid.uuid4(),
        schema_version="1.1",
        session_id=uuid.uuid4(),
        frame_number=0,
        timestamp_seconds=0.0,
        trigger_type=TriggerType.RISK,
        trigger_reason=trigger_reason,
        model_config_id=uuid.uuid4(),
        crowd_metrics_summary=CompactCrowdMetricsSummary(
            risk_score=risk_score, risk_state=risk_state, max_density=0.1, max_pressure=10.0,
            pressure_units_disclaimer="PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS",
            congested_cell_fraction=0.1, reverse_flow_cell_fraction=0.0,
            bottleneck_signal_present=True, density_confidence=0.9,
        ),
        risk_state_snapshot=RiskStateSnapshot(
            frame_number=0, timestamp_seconds=0.0, state=risk_state.value, risk_score=risk_score,
        ),
        vision_observations=vision_observations or [],
        vlm_call_succeeded=True,
        confidence=confidence,
        binding_constraint="density_estimation_confidence",
        complete=complete,
        missing=missing or [],
        contradictions=contradictions or [],
        predictive_projection_snapshot=predictive_projection_snapshot,
    )


def _hazard_observation() -> VisionObservation:
    return VisionObservation(
        observation_id=uuid.uuid4(),
        category=ObservationCategory.VISIBLE_COMPRESSION,
        description=(
            "Dense crowd tightly compressed against a barrier with people "
            "visibly unable to move, several appear distressed."
        ),
        region=NormalizedBoundingBox(x_min=0.2, y_min=0.2, x_max=0.7, y_max=0.7),
        confidence=0.9,
        evidence_type=EvidenceType.DIRECT,
    )


def test_abstention_short_circuit_never_calls_llm():
    reasoner = Reasoner()
    reasoner._client.chat = MagicMock()

    low_confidence_package = _package(confidence=settings.DECISION_CONFIDENCE_FLOOR - 0.01)
    result = reasoner.reason(low_confidence_package)

    assert result.outcome == DecisionOutcome.ABSTAIN
    assert result.abstention_reason is not None
    assert result.recommendation is None
    reasoner._client.chat.assert_not_called()


def test_schema_field_order_evidence_cited_before_outcome():
    schema = _LLMDecisionDraft.model_json_schema()
    keys = list(schema["properties"].keys())
    assert keys.index("evidence_cited") < keys.index("outcome")


def test_llm_unavailable_raises_not_silent_fabrication(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://localhost:1")
    monkeypatch.setattr(settings, "LLM_REQUEST_TIMEOUT_SECONDS", 3.0)

    with pytest.raises(LLMUnavailableError):
        Reasoner()


def test_real_inference_clean_case_produces_well_formed_decision():
    reasoner = Reasoner()
    package = _package(
        risk_score=25.0, risk_state=RiskState.NORMAL,
        vision_observations=[], confidence=0.85,
    )

    result: DecisionResult = reasoner.reason(package)

    assert isinstance(result, DecisionResult)
    # Frozen Decision: confidence is propagated, never invented.
    assert result.confidence == package.confidence
    assert result.binding_constraint == package.binding_constraint
    assert len(result.evidence_cited) >= 1
    if result.outcome in (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH):
        assert result.recommendation is not None
        assert result.recommendation_rationale is not None
    else:
        assert result.recommendation is None
        assert result.recommendation_rationale is None


def test_real_inference_with_projection_snapshot_narrates_not_invents():
    reasoner = Reasoner()
    snapshot = PredictiveProjectionSnapshot(
        projected_pressure=82.5, horizon_seconds=30.0, r_squared=0.87,
    )
    package = _package(
        risk_score=45.0, risk_state=RiskState.ELEVATED,
        vision_observations=[], confidence=0.85,
        predictive_projection_snapshot=snapshot,
    )

    result = reasoner.reason(package)

    assert result.projection_narrative is not None
    numbers = [float(n) for n in re.findall(r"\d+\.?\d*", result.projection_narrative)]
    # Basic sanity check (deliberately generous — natural-language rounding
    # is expected): at least one number in the narrative must be close to
    # the ALREADY-COMPUTED projected_pressure, proving the model narrated
    # rather than invented an unrelated figure.
    assert any(abs(n - snapshot.projected_pressure) <= max(10.0, snapshot.projected_pressure * 0.15) for n in numbers)


def test_real_inference_without_projection_snapshot_narrative_is_null():
    reasoner = Reasoner()
    package = _package(
        risk_score=25.0, risk_state=RiskState.NORMAL,
        vision_observations=[], confidence=0.85, predictive_projection_snapshot=None,
    )

    result = reasoner.reason(package)

    assert result.projection_narrative is None


def test_real_inference_crisis_case_yields_actionable_outcome_with_recommendation():
    reasoner = Reasoner()
    package = _package(
        risk_score=95.0, risk_state=RiskState.CRITICAL,
        vision_observations=[_hazard_observation()], confidence=0.85,
        trigger_reason="risk state escalated ELEVATED->CRITICAL",
    )

    result = reasoner.reason(package)

    assert result.outcome in (DecisionOutcome.INCIDENT, DecisionOutcome.WATCH)
    assert result.recommendation is not None
    assert result.recommendation_rationale is not None
    assert len(result.evidence_cited) >= 1
