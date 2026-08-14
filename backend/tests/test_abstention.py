"""Phase 17, Step 7: should_abstain() — each of the three conditions
independently triggers the correct reason; a genuinely clean case returns
None."""

import uuid

from app.core.config import settings
from app.pipeline.evidence_package import Contradiction, EvidencePackageResult, RiskStateSnapshot
from app.pipeline.abstention import should_abstain
from app.pipeline.risk_state import RiskState
from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import CompactCrowdMetricsSummary


def _package(
    confidence: float = 0.9,
    contradictions: list[Contradiction] = None,
    complete: bool = True,
    missing: list[str] = None,
) -> EvidencePackageResult:
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
            risk_score=50.0, risk_state=RiskState.NORMAL, max_density=0.1, max_pressure=10.0,
            pressure_units_disclaimer="test", congested_cell_fraction=0.0,
            reverse_flow_cell_fraction=0.0, bottleneck_signal_present=True, density_confidence=0.9,
        ),
        risk_state_snapshot=RiskStateSnapshot(
            frame_number=0, timestamp_seconds=0.0, state=RiskState.NORMAL.value, risk_score=50.0,
        ),
        vision_observations=[],
        vlm_call_succeeded=True,
        confidence=confidence,
        binding_constraint="density_estimation_confidence",
        complete=complete,
        missing=missing or [],
        contradictions=contradictions or [],
    )


def test_low_confidence_triggers_abstention():
    package = _package(confidence=settings.DECISION_CONFIDENCE_FLOOR - 0.01)
    reason = should_abstain(package)
    assert reason is not None
    assert "confidence" in reason


def test_unresolved_contradiction_triggers_abstention():
    package = _package(
        contradictions=[Contradiction(contradiction_type="test_type", description="test")]
    )
    reason = should_abstain(package)
    assert reason is not None
    assert "contradiction" in reason


def test_incomplete_evidence_triggers_abstention():
    package = _package(complete=False, missing=["bottleneck_signal"])
    reason = should_abstain(package)
    assert reason is not None
    assert "incomplete" in reason


def test_clean_case_does_not_abstain():
    package = _package(
        confidence=0.9, contradictions=[], complete=True, missing=[]
    )
    assert should_abstain(package) is None


def test_confidence_exactly_at_floor_triggers_abstention():
    # Boundary case: DECISION_CONFIDENCE_FLOOR (0.4) is set to density.py's
    # own TOO_FEW_POINTS_CONFIDENCE tier — the worst confidence this
    # pipeline ever systematically produces. should_abstain() uses an
    # INCLUSIVE "<=" comparison specifically so this exact worst-known tier
    # does NOT narrowly skate through without abstaining.
    package = _package(confidence=settings.DECISION_CONFIDENCE_FLOOR)
    reason = should_abstain(package)
    assert reason is not None
    assert "confidence" in reason


def test_confidence_just_above_floor_does_not_abstain_on_confidence():
    package = _package(confidence=settings.DECISION_CONFIDENCE_FLOOR + 0.01)
    assert should_abstain(package) is None
