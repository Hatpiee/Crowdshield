"""Phase 17, Step 7: Reasoner tests. Includes REAL (non-mocked) inference
tests against the actually-pulled qwen3:8b tag — same "never claim tested
without testing" standard as Phase 14's test_minicpm_vlm.py."""

import json
import re
import uuid
from unittest.mock import MagicMock

import pytest

from app.core.config import settings
from app.pipeline.decision_result import (
    DecisionOutcome,
    DecisionResult,
    EventClassification,
    _LLMDecisionDraft,
)
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


def _fake_chat_response(payload: dict):
    """Mocked ollama ChatResponse-shaped object — reasoner.py only ever
    reads `response.message.content`, so this is the minimal real shape."""

    class _Message:
        content = json.dumps(payload)

    class _Response:
        message = _Message()

    return _Response()


def _incident_payload(event_classification=None) -> dict:
    return {
        "evidence_cited": ["risk_score"],
        "outcome": "INCIDENT",
        "reasoning_summary": "Dense crowd compression observed against a barrier.",
        "recommendation": "DEPLOY_ADDITIONAL_SECURITY",
        "recommendation_rationale": "Crowd compression requires immediate intervention.",
        "projection_narrative": None,
        "event_classification": event_classification,
        "structured_report": {
            "event_summary": "Crowd compression detected near the barrier.",
            "observed_evidence": ["dense crowd compression", "reduced individual mobility"],
            "behavioral_analysis": "People appear unable to move freely.",
            "spatial_analysis": "Localized to one region near the barrier.",
            "temporal_analysis": "Onset within the last few observed frames.",
            "crowd_risk_context": "risk_score=95.0 (CRITICAL) is consistent with the observed compression.",
        },
    }


def test_missing_event_classification_on_incident_defaults_to_unknown_without_retry():
    """Regression guard (Reasoner Stability phase): reasoner.py's DecisionResult
    construction previously never passed draft.event_classification/
    draft.structured_report through at all, so EVERY INCIDENT/WATCH outcome
    unconditionally failed business-rule validation and retried — this test
    proves BOTH that a genuinely omitted event_classification is handled
    with a single, honest, deterministic UNKNOWN default, AND that no
    retry (no second real/mocked call) is burned doing it."""
    reasoner = Reasoner()
    reasoner._client.chat = MagicMock(return_value=_fake_chat_response(_incident_payload(event_classification=None)))

    package = _package(
        risk_score=95.0, risk_state=RiskState.CRITICAL, vision_observations=[_hazard_observation()],
    )
    result = reasoner.reason(package)

    assert result.outcome == DecisionOutcome.INCIDENT
    assert result.event_classification == EventClassification.UNKNOWN
    reasoner._client.chat.assert_called_once()


def _watch_payload(with_structured_report: bool) -> dict:
    payload = {
        "evidence_cited": ["risk_score"],
        "outcome": "WATCH",
        "reasoning_summary": "Elevated pressure observed but not yet incident-worthy.",
        "recommendation": "DEPLOY_ADDITIONAL_SECURITY",
        "recommendation_rationale": "Precautionary staging given rising pressure.",
        "projection_narrative": None,
        "event_classification": "CROWD_CRUSH",
        "structured_report": None,
    }
    if with_structured_report:
        payload["structured_report"] = {
            "event_summary": "Pressure rising near the barrier, not yet critical.",
            "observed_evidence": ["rising crowd pressure", "partial congestion"],
            "behavioral_analysis": "Crowd movement slowed but remains orderly.",
            "spatial_analysis": "Localized to one region near the barrier.",
            "temporal_analysis": "Gradual onset over the last several frames.",
            "crowd_risk_context": "risk_score=55.0 (ELEVATED) is consistent with the observation.",
        }
    return payload


def test_watch_with_structured_report_is_valid_no_retry():
    """Semantic Admission Control phase — WATCH/structured_report contract
    fix regression: the real production defect was a WATCH decision that
    legitimately carried a structured_report, which the OLD validator
    rejected (forcing a retry that burned 390.51s before ultimately
    failing). A WATCH decision with a well-formed structured_report must
    now validate on the FIRST attempt, no retry."""
    reasoner = Reasoner()
    reasoner._client.chat = MagicMock(
        return_value=_fake_chat_response(_watch_payload(with_structured_report=True))
    )

    package = _package(
        risk_score=55.0, risk_state=RiskState.ELEVATED, vision_observations=[_hazard_observation()],
    )
    result = reasoner.reason(package)

    assert result.outcome == DecisionOutcome.WATCH
    assert result.structured_report is not None
    assert result.structured_report.event_summary == (
        _watch_payload(with_structured_report=True)["structured_report"]["event_summary"]
    )
    reasoner._client.chat.assert_called_once()


def test_watch_without_structured_report_is_still_valid_no_retry():
    """WATCH with structured_report left null (the common/original path)
    must remain valid — structured_report is optional, not required, for
    WATCH."""
    reasoner = Reasoner()
    reasoner._client.chat = MagicMock(
        return_value=_fake_chat_response(_watch_payload(with_structured_report=False))
    )

    package = _package(
        risk_score=55.0, risk_state=RiskState.ELEVATED, vision_observations=[_hazard_observation()],
    )
    result = reasoner.reason(package)

    assert result.outcome == DecisionOutcome.WATCH
    assert result.structured_report is None
    reasoner._client.chat.assert_called_once()


def test_incident_without_structured_report_still_fails_validation():
    """The contract relaxation applies ONLY to WATCH — INCIDENT must still
    REQUIRE a structured_report; a model response omitting it on INCIDENT
    is a genuine validation failure, not silently accepted."""
    reasoner = Reasoner()
    bad_payload = _incident_payload(event_classification="CROWD_CRUSH")
    bad_payload["structured_report"] = None
    reasoner._client.chat = MagicMock(return_value=_fake_chat_response(bad_payload))
    # Bound retries to keep this test fast — every attempt will fail the
    # same way (structured_report stays None from the same mocked response).
    from app.pipeline import reasoner as reasoner_module
    from unittest.mock import patch

    with patch.object(reasoner_module.settings, "LLM_MAX_RETRIES", 0):
        from app.pipeline.reasoner import LLMResponseValidationError

        with pytest.raises(LLMResponseValidationError):
            reasoner.reason(
                _package(
                    risk_score=95.0, risk_state=RiskState.CRITICAL,
                    vision_observations=[_hazard_observation()],
                )
            )


def test_no_incident_with_structured_report_still_fails_validation():
    """NO_INCIDENT must still FORBID a structured_report — the relaxation
    is WATCH-only, not a blanket "always optional" change."""
    reasoner = Reasoner()
    bad_payload = {
        "evidence_cited": ["risk_score"],
        "outcome": "NO_INCIDENT",
        "reasoning_summary": "Nothing of note observed.",
        "recommendation": None,
        "recommendation_rationale": None,
        "projection_narrative": None,
        "event_classification": None,
        "structured_report": {
            "event_summary": "Should not be here.",
            "observed_evidence": ["x"],
            "behavioral_analysis": "x",
            "spatial_analysis": "x",
            "temporal_analysis": "x",
            "crowd_risk_context": "x",
        },
    }
    reasoner._client.chat = MagicMock(return_value=_fake_chat_response(bad_payload))
    from unittest.mock import patch

    with patch.object(settings, "LLM_MAX_RETRIES", 0):
        from app.pipeline.reasoner import LLMResponseValidationError

        with pytest.raises(LLMResponseValidationError):
            reasoner.reason(_package(risk_score=10.0, risk_state=RiskState.NORMAL))


def test_event_classification_and_structured_report_propagate_from_model_output():
    """Regression guard: when the model DOES supply event_classification, it
    must actually reach the persisted DecisionResult unchanged — proves the
    call site's field-wiring bug (found and fixed this phase) stays fixed."""
    reasoner = Reasoner()
    payload = _incident_payload(event_classification="EXPLOSIVE_EVENT")
    reasoner._client.chat = MagicMock(return_value=_fake_chat_response(payload))

    package = _package(
        risk_score=95.0, risk_state=RiskState.CRITICAL, vision_observations=[_hazard_observation()],
    )
    result = reasoner.reason(package)

    assert result.event_classification == EventClassification.EXPLOSIVE_EVENT
    assert result.structured_report is not None
    assert result.structured_report.event_summary == payload["structured_report"]["event_summary"]
    assert result.structured_report.observed_evidence == payload["structured_report"]["observed_evidence"]
    reasoner._client.chat.assert_called_once()


def test_ollama_num_thread_forwarded_when_configured(monkeypatch):
    """Final CPU Stabilization phase: settings.OLLAMA_NUM_THREAD, when set,
    must be forwarded as options["num_thread"] on the real chat() call —
    a VERIFIED real field on the installed ollama client's Options model
    (see config.py's own docstring), not an invented parameter."""
    monkeypatch.setattr(settings, "OLLAMA_NUM_THREAD", 8)
    reasoner = Reasoner()
    reasoner._client.chat = MagicMock(return_value=_fake_chat_response(_watch_payload(with_structured_report=False)))

    reasoner.reason(_package(risk_score=55.0, risk_state=RiskState.ELEVATED))

    _, kwargs = reasoner._client.chat.call_args
    assert kwargs["options"]["num_thread"] == 8


def test_ollama_num_thread_absent_when_unconfigured(monkeypatch):
    monkeypatch.setattr(settings, "OLLAMA_NUM_THREAD", None)
    reasoner = Reasoner()
    reasoner._client.chat = MagicMock(return_value=_fake_chat_response(_watch_payload(with_structured_report=False)))

    reasoner.reason(_package(risk_score=55.0, risk_state=RiskState.ELEVATED))

    _, kwargs = reasoner._client.chat.call_args
    assert "num_thread" not in kwargs["options"]


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


def test_real_inference_watch_case_never_raises_regardless_of_structured_report():
    """Semantic Admission Control phase — real-inference regression for the
    actual production defect (see DECISIONS.md): a real ACUTE_HAZARD-style
    WATCH decision previously failed validation and burned a 390.51s retry
    loop whenever Qwen3 chose to attach a structured_report to a WATCH
    outcome. This evidence shape (elevated-but-not-critical risk, one
    hazard-consistent observation, ACUTE_HAZARD-flavored trigger_reason) is
    designed to plausibly elicit WATCH, matching the real failure's
    conditions. The model's own choice of whether to attach a
    structured_report is NOT forced (non-deterministic) — the fix is
    proven by this call completing without LLMResponseValidationError no
    matter which way the model goes, not by asserting the outcome exactly."""
    reasoner = Reasoner()
    package = _package(
        risk_score=55.0, risk_state=RiskState.ELEVATED,
        vision_observations=[_hazard_observation()], confidence=0.85,
        trigger_reason="acute hazard signals corroborated: motion_energy, scene_change",
    )

    result = reasoner.reason(package)  # must not raise LLMResponseValidationError

    assert isinstance(result, DecisionResult)
    if result.outcome == DecisionOutcome.WATCH:
        # Whatever the model chose, it is a VALID DecisionResult already
        # (construction would have raised otherwise) — structured_report is
        # either a well-formed report or None, both legitimate for WATCH.
        assert result.structured_report is None or result.structured_report.event_summary


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
