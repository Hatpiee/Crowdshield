"""Phase 16, Step 6: EvidenceBuilder pure-logic tests against synthetic
inputs. Only the two file-saving-focused assertions actually inspect disk
(EVIDENCE_FRAMES_STORAGE_PATH is redirected to a per-test temp dir by the
autouse `_evidence_storage_tmp` conftest fixture) — build() always writes
real image files as part of its implementation (Step 2), so every test here
exercises that path, but only the dedicated file-saving test asserts on it.
"""

import uuid

import numpy as np

from app.pipeline.bottleneck import BottleneckField
from app.pipeline.congestion import CongestionField
from app.pipeline.core_crowd_metrics import CoreCrowdMetrics
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import DensityField
from app.pipeline.decision_result import EventClassification, EventReportSections
from app.pipeline.evidence_builder import EvidenceBuilder
from app.pipeline.frame import Frame
from app.pipeline.reverse_flow import ReverseFlowField
from app.pipeline.risk_score import RiskScoreResult
from app.pipeline.risk_state import RiskState, RiskStateResult
from app.pipeline.trigger_engine import TriggerDecision, TriggerType
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary,
    EvidenceType,
    NormalizedBoundingBox,
    ObservationCategory,
    VisionAnalysisResult,
    VisionObservation,
)
from app.services import session_service

FRAME_WIDTH = 64
FRAME_HEIGHT = 64
ALL_SIGNALS = ["pressure", "congestion", "bottleneck", "reverse_flow"]


def _frame(frame_number: int = 0, timestamp_seconds: float = 0.0) -> Frame:
    image = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), 128, dtype=np.uint8)
    return Frame(
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        image=image,
        width=FRAME_WIDTH,
        height=FRAME_HEIGHT,
    )


def _crowd_metrics(
    density_confidence: float = 0.9,
    contributing_signals: list[str] = None,
    reverse_flow_cell_fraction: float = 0.0,
    bottleneck_present: bool = True,
) -> CrowdMetrics:
    if contributing_signals is None:
        contributing_signals = list(ALL_SIGNALS)

    density = DensityField(
        frame_number=0, timestamp_seconds=0.0,
        grid=np.array([[0.5]]), track_count=3,
        estimation_confidence=density_confidence,
        degradation_reason=None, voronoi_disagreement_summary=None,
    )
    pressure = CrowdPressureField(
        frame_number=0, timestamp_seconds=0.0,
        grid=np.array([[10.0]]), max_pressure=10.0, mean_pressure=5.0,
    )
    core = CoreCrowdMetrics(
        frame_number=0, timestamp_seconds=0.0, density=density, flow=None, pressure=pressure,
    )
    congestion = CongestionField(
        frame_number=0, timestamp_seconds=0.0,
        is_congested_grid=np.array([[False]]),
        congestion_score_grid=np.array([[0.0]]),
        congested_cell_fraction=0.0,
    )
    reverse_flow = ReverseFlowField(
        frame_number=0, timestamp_seconds=0.0,
        is_reverse_flow_grid=np.array([[reverse_flow_cell_fraction > 0]]),
        reverse_flow_cell_fraction=reverse_flow_cell_fraction,
        cells_with_established_baseline=1,
    )
    bottleneck = None
    if bottleneck_present:
        bottleneck = BottleneckField(
            frame_number=0, timestamp_seconds=0.0, window_frames_used=30,
            bottleneck_score_grid=np.array([[1.0]]), strongest_bottleneck_cell=(0, 0),
        )
    risk_score = RiskScoreResult(
        frame_number=0, timestamp_seconds=0.0, risk_score=50.0, confidence=1.0,
        contributing_signals=contributing_signals,
        sub_scores={signal: 10.0 for signal in contributing_signals},
    )
    return CrowdMetrics(
        frame_number=0, timestamp_seconds=0.0, core=core, congestion=congestion,
        bottleneck=bottleneck, reverse_flow=reverse_flow, risk_score=risk_score,
        predictive_projection=None,
    )


def _risk_state_result(state: RiskState = RiskState.NORMAL) -> RiskStateResult:
    return RiskStateResult(
        frame_number=0, timestamp_seconds=0.0, state=state, risk_score=50.0,
        frames_in_current_candidate_transition=0, incident_threshold_crossed=False,
        state_changed_this_frame=False,
    )


def _trigger_decision() -> TriggerDecision:
    return TriggerDecision(
        frame_number=0, timestamp_seconds=0.0, trigger_type=TriggerType.RISK,
        reason="test trigger",
    )


def _structured_report() -> EventReportSections:
    """Acute-Hazard Trigger Phase: a minimal, valid EventReportSections —
    shared by every test file that constructs a DecisionResult with
    outcome=INCIDENT directly (required by DecisionResult's own validator).
    """
    return EventReportSections(
        event_summary="test event summary",
        observed_evidence=["test observed evidence item"],
        behavioral_analysis="test behavioral analysis",
        spatial_analysis="test spatial analysis",
        temporal_analysis="test temporal analysis",
        crowd_risk_context="test crowd risk context",
    )


def _observation(confidence: float = 0.6, category=ObservationCategory.VISIBLE_HAZARD) -> VisionObservation:
    return VisionObservation(
        observation_id=uuid.uuid4(),
        category=category,
        description="a test observation",
        region=NormalizedBoundingBox(x_min=0.1, y_min=0.1, x_max=0.5, y_max=0.5),
        confidence=confidence,
        evidence_type=EvidenceType.DIRECT,
    )


def _vision_result(observations: list[VisionObservation]) -> VisionAnalysisResult:
    return VisionAnalysisResult(
        frame_number=0, timestamp_seconds=0.0, observations=observations,
        model_name="test-model", model_latency_seconds=1.0,
        sanitization_applied=True, trigger_reason="test trigger",
    )


def _real_session(db_session, make_video, test_user):
    user, _ = test_user
    video = make_video()
    return session_service.create_session(db_session, video.id, user.id)


def test_happy_path_confidence_binding_completeness(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    observation = _observation(confidence=0.6)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(density_confidence=0.9),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([observation]),
        vlm_call_succeeded=True,
    )

    # Hand-verified weakest-link: candidates = [density=0.9, observation=0.6]
    # -> min = 0.6, bound to the observation, not the density value.
    assert result.confidence == 0.6
    assert result.binding_constraint == f"vlm_observation:{observation.observation_id}"
    assert result.complete is True
    assert result.missing == []
    assert result.contradictions == []


def test_empty_but_successful_vlm_result_is_complete(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(density_confidence=0.85),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([]),
        vlm_call_succeeded=True,
    )

    assert result.complete is True
    assert "vision_observations" not in result.missing
    # An empty (but successful) observations list contributes NOTHING to the
    # minimum — confidence is derived from density confidence alone.
    assert result.confidence == 0.85
    assert result.binding_constraint == "density_estimation_confidence"


def test_failed_vlm_call_marks_incomplete_but_confidence_still_computed(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(density_confidence=0.7),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=None,
        vlm_call_succeeded=False,
    )

    assert result.complete is False
    assert "vision_observations" in result.missing
    assert result.confidence == 0.7
    assert result.binding_constraint == "density_estimation_confidence"


def test_unavailable_bottleneck_signal_surfaces_in_missing(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(
            contributing_signals=["pressure", "congestion", "reverse_flow"],
            bottleneck_present=False,
        ),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([]),
        vlm_call_succeeded=True,
    )

    assert "bottleneck_signal" in result.missing
    assert result.complete is False


def test_reverse_flow_contradiction_detected_and_unresolved(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(reverse_flow_cell_fraction=0.3),
        risk_state_result=_risk_state_result(state=RiskState.NORMAL),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([]),  # zero UNUSUAL_MOVEMENT observations
        vlm_call_succeeded=True,
    )

    types = [c.contradiction_type for c in result.contradictions]
    assert "reverse_flow_not_visually_confirmed" in types
    for contradiction in result.contradictions:
        assert contradiction.resolution_status == "UNRESOLVED"


def test_critical_risk_no_visual_evidence_contradiction_detected(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(reverse_flow_cell_fraction=0.0),
        risk_state_result=_risk_state_result(state=RiskState.CRITICAL),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([]),
        vlm_call_succeeded=True,
    )

    types = [c.contradiction_type for c in result.contradictions]
    assert "critical_risk_no_visual_evidence" in types


def test_consistent_case_raises_no_false_contradiction(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    observation = _observation(category=ObservationCategory.UNUSUAL_MOVEMENT)
    result = EvidenceBuilder().build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(reverse_flow_cell_fraction=0.3),
        risk_state_result=_risk_state_result(state=RiskState.NORMAL),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([observation]),
        vlm_call_succeeded=True,
    )

    assert result.contradictions == []


def test_representative_frame_and_roi_crop_saved_to_disk(db_session, make_video, test_user):
    session = _real_session(db_session, make_video, test_user)
    builder = EvidenceBuilder()
    result = builder.build(
        db=db_session, session_id=session.id, frame=_frame(),
        crowd_metrics=_crowd_metrics(),
        risk_state_result=_risk_state_result(),
        trigger_decision=_trigger_decision(),
        roi_bbox=(5.0, 5.0, 40.0, 40.0),
        vision_result=_vision_result([]),
        vlm_call_succeeded=True,
    )

    storage_dir = builder.get_storage_dir()
    frame_path = storage_dir / result.representative_frame_path
    roi_path = storage_dir / result.roi_crop_path

    assert frame_path.exists()
    assert roi_path.exists()
    assert result.representative_frame_path == f"{session.id}/{result.package_id}_frame.jpg"
    assert result.roi_crop_path == f"{session.id}/{result.package_id}_roi.jpg"
