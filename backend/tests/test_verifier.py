"""Phase 18, Step 7: Verifier tests. Includes REAL (non-mocked) inference
tests against the actually-pulled qwen3:8b tag with think=True — same
"never claim tested without testing" standard as Phase 14/17."""

import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.pipeline.decision_result import (
    DecisionOutcome,
    DecisionResult,
    EventClassification,
    EventReportSections,
    RecommendationType,
)
from app.pipeline.evidence_package import (
    EvidencePackageResult, PredictiveProjectionSnapshot, RiskStateSnapshot,
)
from app.pipeline.risk_state import RiskState
from app.pipeline.trigger_engine import TriggerType
from app.pipeline.verifier import LLMVerificationUnavailableError, Verifier
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary, EvidenceType, NormalizedBoundingBox,
    ObservationCategory, VisionObservation,
)

# REAL data, verbatim from a real Phase 17 preview-script-persisted row
# (evidence_package_id=52e0b423-de06-4e2a-86f0-b9b1325f6c43,
# decision_id=f8e889a7-b700-4dcc-b26d-27a9e8c21030) — the SAME real pair
# used for Step 0's latency measurement, not a toy synthetic example.
_REAL_OBSERVATION_ID = uuid.UUID("9f71b342-26ae-4eb1-a3bc-9cce57703fce")


def _real_evidence_package() -> EvidencePackageResult:
    return EvidencePackageResult(
        package_id=uuid.UUID("52e0b423-de06-4e2a-86f0-b9b1325f6c43"),
        schema_version="1.1", session_id=uuid.uuid4(), frame_number=214,
        timestamp_seconds=7.133333333333326, trigger_type=TriggerType.RISK,
        trigger_reason="risk state escalated ELEVATED->CRITICAL (cooldown override: new higher escalation)",
        model_config_id=uuid.uuid4(),
        crowd_metrics_summary=CompactCrowdMetricsSummary(
            risk_score=91.0, risk_state=RiskState.CRITICAL, max_density=0.17114181093603886,
            max_pressure=17.380169538619594, density_confidence=0.5, congested_cell_fraction=0.0,
            bottleneck_signal_present=True,
            pressure_units_disclaimer="PIXEL-SPACE UNITS, NOT METERS (see DECISIONS.md).",
            reverse_flow_cell_fraction=0.0,
        ),
        risk_state_snapshot=RiskStateSnapshot(
            state="CRITICAL", risk_score=91.0, frame_number=214, timestamp_seconds=7.133333333333326,
        ),
        vision_observations=[
            VisionObservation(
                observation_id=_REAL_OBSERVATION_ID, category=ObservationCategory.BOTTLENECK,
                description=(
                    "The area shows a concentration of people and vehicles with "
                    "limited space, suggesting a potential bottleneck."
                ),
                region=NormalizedBoundingBox(x_min=0.0, y_min=0.0, x_max=1.0, y_max=0.5),
                confidence=0.8, evidence_type=EvidenceType.INFERRED,
            )
        ],
        vlm_call_succeeded=True, confidence=0.5, binding_constraint="density_estimation_confidence",
        complete=True, missing=[], contradictions=[],
        predictive_projection_snapshot=PredictiveProjectionSnapshot(
            projected_pressure=9.461309925203896, horizon_seconds=30.0, r_squared=0.4148563749716443,
        ),
    )


def _real_well_grounded_decision() -> DecisionResult:
    package = _real_evidence_package()
    return DecisionResult(
        decision_id=uuid.UUID("f8e889a7-b700-4dcc-b26d-27a9e8c21030"),
        evidence_package_id=package.package_id,
        evidence_cited=["risk_score", "risk_state", "bottleneck_signal_present", str(_REAL_OBSERVATION_ID)],
        outcome=DecisionOutcome.INCIDENT,
        reasoning_summary=(
            "The risk score is at a critical level (91.0), and the system has "
            "escalated the risk state to CRITICAL. Additionally, the "
            "bottleneck signal is present, indicating a potential for incident "
            "due to high pressure and density in the area."
        ),
        recommendation=RecommendationType.DEPLOY_ADDITIONAL_SECURITY,
        recommendation_rationale=(
            "The high risk score and presence of a bottleneck signal indicate "
            "a critical situation where additional security resources are "
            "needed to manage the crowd and prevent potential incidents."
        ),
        projection_narrative=(
            "The projected pressure is 9.46, which is expected to occur within "
            "the next 30 seconds. The model has a moderate confidence level "
            "with an R-squared value of 0.41."
        ),
        event_classification=EventClassification.CROWD_CRUSH,
        structured_report=EventReportSections(
            event_summary="test event summary",
            observed_evidence=["test observed evidence item"],
            behavioral_analysis="test behavioral analysis",
            spatial_analysis="test spatial analysis",
            temporal_analysis="test temporal analysis",
            crowd_risk_context="test crowd risk context",
        ),
        abstention_reason=None, confidence=0.5, binding_constraint="density_estimation_confidence",
    )


def test_deterministic_short_circuit_never_calls_llm():
    verifier = Verifier()
    verifier._client.chat = MagicMock()

    package = _real_evidence_package()
    decision = _real_well_grounded_decision()
    # Deliberately break confidence_consistency — a real bug scenario, not
    # a model behavior question.
    decision = decision.model_copy(update={"confidence": 0.99})

    result = verifier.verify(decision, package)

    assert result.passed is False
    assert result.confidence_consistency_ok is False
    assert result.llm_checks is None
    verifier._client.chat.assert_not_called()


def _fake_verification_response() -> MagicMock:
    import json

    payload = {
        "reasoning_consistency_ok": True, "reasoning_consistency_note": "ok",
        "contradiction_handling_ok": True, "contradiction_handling_note": "ok",
        "recommendation_consistency_ok": True, "recommendation_consistency_note": "ok",
        "unsupported_claims_found": False, "unsupported_claims_note": "ok",
        "overall_verdict": "CONFIRMED",
    }

    class _Message:
        content = json.dumps(payload)

    class _Response:
        message = _Message()

    return _Response()


def test_ollama_num_thread_forwarded_when_configured(monkeypatch):
    """Final Release Hardening phase, Step 2: settings.OLLAMA_NUM_THREAD,
    when set, must be forwarded as options["num_thread"] on the real
    chat() call — traces the ACTUAL runtime call, not just config.py.
    Mirrors the identical regression tests in test_reasoner.py/
    test_minicpm_vlm.py for the other two real call sites."""
    monkeypatch.setattr(settings, "OLLAMA_NUM_THREAD", 4)
    verifier = Verifier()
    verifier._client.chat = MagicMock(return_value=_fake_verification_response())

    package = _real_evidence_package()
    decision = _real_well_grounded_decision()
    verifier.verify(decision, package)

    _, kwargs = verifier._client.chat.call_args
    assert kwargs["options"]["num_thread"] == 4


def test_ollama_num_thread_absent_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_NUM_THREAD", None)
    verifier = Verifier()
    verifier._client.chat = MagicMock(return_value=_fake_verification_response())

    package = _real_evidence_package()
    decision = _real_well_grounded_decision()
    verifier.verify(decision, package)

    _, kwargs = verifier._client.chat.call_args
    assert "num_thread" not in kwargs["options"]


def test_llm_verification_unavailable_raises(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_BASE_URL", "http://localhost:1")
    monkeypatch.setattr(settings, "VERIFIER_REQUEST_TIMEOUT_SECONDS", 3.0)

    with pytest.raises(LLMVerificationUnavailableError):
        Verifier()


def test_real_verification_of_well_grounded_incident_decision_passes_and_uses_think_true():
    verifier = Verifier()
    package = _real_evidence_package()
    decision = _real_well_grounded_decision()

    captured_kwargs = {}
    original_chat = verifier._client.chat

    def _spy_chat(*args, **kwargs):
        captured_kwargs.update(kwargs)
        return original_chat(*args, **kwargs)

    verifier._client.chat = _spy_chat

    result = verifier.verify(decision, package)

    assert captured_kwargs.get("think") is True
    assert result.confidence_consistency_ok is True
    assert result.evidence_grounding_existence_ok is True
    assert result.llm_checks is not None
    assert result.passed is True, f"Expected a well-grounded real decision to pass verification; issues: {result.issues_found}"


def test_real_verification_catches_deliberately_unsupported_claim():
    verifier = Verifier()
    package = _real_evidence_package()
    well_grounded = _real_well_grounded_decision()
    # Deliberately inject a plausible-sounding but FABRICATED claim not
    # traceable to any evidence actually provided (no camera-count
    # observation exists anywhere in this package).
    fabricated = well_grounded.model_copy(update={
        "reasoning_summary": (
            well_grounded.reasoning_summary
            + " Security cameras confirm exactly 47 people are currently "
            "trapped with no visible exit route."
        ),
    })

    result = verifier.verify(fabricated, package)

    # Honest report, not a forced pass: this is what we EXPECT a working
    # unsupported-claims check to catch. If real behavior differs, this
    # assertion fails and that is reported truthfully, not hidden.
    assert result.llm_checks is not None
    assert result.passed is False, (
        "Expected the unsupported_claims check to flag the fabricated "
        f"'47 people trapped' claim, but verification passed. llm_checks={result.llm_checks}"
    )
    assert result.llm_checks.unsupported_claims_found is True
