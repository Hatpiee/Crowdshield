"""EvidenceBuilder — roadmap Phase 15 (this project's Phase 16), §28's exact
contract: `EvidenceBuilder.build(inputs...) -> EvidencePackage`. The clean,
testable component that binds Crowd Intelligence Engine, Risk State, and
Vision Intelligence outputs into one structural EvidencePackageResult.

SCOPE BOUNDARY: this does NOT call the VLM itself (the caller's job, exactly
as established since Phase 14 — see minicpm_vlm.py) and does NOT decide the
real-world cadence of when evidence gets gathered (an orchestration decision
belonging to a later phase — see scripts/preview_evidence_package.py's own
demonstration-only cadence). The caller has ALREADY decided evidence-
gathering is warranted (a non-NONE TriggerDecision already exists) before
calling build().

Stateless / pure-function-style class: unlike Tracker/BottleneckDetector,
there is no cross-call state to hold — each build() call handles one
independent trigger event end-to-end.
"""

import uuid
from pathlib import Path

import cv2
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, settings
from app.models.analysis_session import AnalysisSession
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.evidence_package import (
    Contradiction,
    EvidencePackageResult,
    RiskStateSnapshot,
    SCHEMA_VERSION,
)
from app.pipeline.frame import Frame
from app.pipeline.risk_state import RiskState, RiskStateResult
from app.pipeline.trigger_engine import TriggerDecision
from app.pipeline.vision_observation import (
    CompactCrowdMetricsSummary,
    ObservationCategory,
    VisionAnalysisResult,
)

JPEG_QUALITY = 90

# The full, canonical vocabulary of RiskScoreResult.contributing_signals
# (Phase 11, risk_score.py's own module-level _SIGNAL_ORDER) — duplicated
# here rather than importing the private name, since this is a stable,
# already-documented public contract (each string is one of exactly these
# four values, per CompactCrowdMetricsSummary's own field set).
ALL_RISK_SUB_SIGNALS = ("pressure", "congestion", "bottleneck", "reverse_flow")

# RiskState values for which "the VLM call succeeded but saw nothing" is
# itself worth flagging as a contradiction (decision #3b). INCIDENT is
# included for structural completeness even though it is not reachable yet
# (Phase 13's Resolution 2) — no harm in the check existing ahead of time.
_HIGH_RISK_STATES = (RiskState.CRITICAL, RiskState.INCIDENT)


class EvidenceBuilder:
    def get_storage_dir(self) -> Path:
        # Same cwd-relative-path resolution pattern as VIDEO_STORAGE_PATH /
        # HEATMAP_STORAGE_PATH (video_storage.py / heatmap_service.py).
        raw = Path(settings.EVIDENCE_FRAMES_STORAGE_PATH)
        path = raw if raw.is_absolute() else REPO_ROOT / raw
        path.mkdir(parents=True, exist_ok=True)
        return path

    def _save_images(
        self,
        session_id: uuid.UUID,
        package_id: uuid.UUID,
        frame: Frame,
        roi_bbox: tuple[float, float, float, float],
    ) -> tuple[str, str]:
        storage_dir = self.get_storage_dir()
        session_dir = storage_dir / str(session_id)
        session_dir.mkdir(parents=True, exist_ok=True)

        frame_relative_path = f"{session_id}/{package_id}_frame.jpg"
        roi_relative_path = f"{session_id}/{package_id}_roi.jpg"

        frame_destination = storage_dir / frame_relative_path
        success, encoded = cv2.imencode(
            ".jpg", frame.image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not success:
            raise RuntimeError("Failed to JPEG-encode the representative frame")
        frame_destination.write_bytes(encoded.tobytes())

        x_min, y_min, x_max, y_max = (int(round(v)) for v in roi_bbox)
        x_min = max(0, min(x_min, frame.image.shape[1] - 1))
        x_max = max(x_min + 1, min(x_max, frame.image.shape[1]))
        y_min = max(0, min(y_min, frame.image.shape[0] - 1))
        y_max = max(y_min + 1, min(y_max, frame.image.shape[0]))
        roi_crop = frame.image[y_min:y_max, x_min:x_max]

        roi_destination = storage_dir / roi_relative_path
        success, encoded = cv2.imencode(
            ".jpg", roi_crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY]
        )
        if not success:
            raise RuntimeError("Failed to JPEG-encode the ROI crop")
        roi_destination.write_bytes(encoded.tobytes())

        return frame_relative_path, roi_relative_path

    def _compute_confidence(
        self, crowd_metrics: CrowdMetrics, vision_observations: list
    ) -> tuple[float, str]:
        # Decision #1: MINIMUM/weakest-link rule (§17, "Confidence
        # Propagation, Never Invention"). density.estimation_confidence is
        # always present; an EMPTY vision_observations list contributes
        # NOTHING to the minimum (no VLM-sourced claim to be uncertain
        # about) — consistent with this project's "empty is a valid,
        # non-degraded outcome" principle.
        density_confidence = crowd_metrics.core.density.estimation_confidence
        best = ("density_estimation_confidence", density_confidence)

        for observation in vision_observations:
            candidate = (f"vlm_observation:{observation.observation_id}", observation.confidence)
            if candidate[1] < best[1]:
                best = candidate

        return best[1], best[0]

    def _compute_completeness(
        self, crowd_metrics: CrowdMetrics, vlm_call_succeeded: bool
    ) -> list[str]:
        missing: list[str] = []
        if not vlm_call_succeeded:
            missing.append("vision_observations")

        # Reuse of Phase 11's contributing_signals tracking (decision #2) —
        # any signal absent that cycle (e.g. bottleneck's rolling window not
        # yet filled) is genuinely missing evidence, surfaced here rather
        # than silently treated as "just not applicable."
        contributing = set(crowd_metrics.risk_score.contributing_signals)
        for signal in ALL_RISK_SUB_SIGNALS:
            if signal not in contributing:
                missing.append(f"{signal}_signal")

        return missing

    def _detect_contradictions(
        self,
        crowd_metrics: CrowdMetrics,
        risk_state_result: RiskStateResult,
        vision_observations: list,
        vlm_call_succeeded: bool,
    ) -> list[Contradiction]:
        # Decision #3: a SMALL, explicitly non-exhaustive, engineering-
        # judgment starting set — not a claimed-complete taxonomy. Every
        # detected contradiction is resolution_status="UNRESOLVED" always;
        # there is no reasoning layer yet (Decision Intelligence) to
        # actually resolve anything, only detect and record.
        contradictions: list[Contradiction] = []
        if not vlm_call_succeeded:
            # A failed call has no visual evidence to contradict anything
            # with — that's a "missing" state (decision #2), not a
            # contradiction.
            return contradictions

        if crowd_metrics.reverse_flow is not None and crowd_metrics.reverse_flow.reverse_flow_cell_fraction > 0:
            has_unusual_movement = any(
                obs.category == ObservationCategory.UNUSUAL_MOVEMENT for obs in vision_observations
            )
            if not has_unusual_movement:
                contradictions.append(
                    Contradiction(
                        contradiction_type="reverse_flow_not_visually_confirmed",
                        description=(
                            "reverse_flow_cell_fraction="
                            f"{crowd_metrics.reverse_flow.reverse_flow_cell_fraction:.4f} > 0 "
                            "but the VLM reported zero UNUSUAL_MOVEMENT observations."
                        ),
                    )
                )

        if risk_state_result.state in _HIGH_RISK_STATES and not vision_observations:
            contradictions.append(
                Contradiction(
                    contradiction_type="critical_risk_no_visual_evidence",
                    description=(
                        f"risk_state={risk_state_result.state.value} but a successful "
                        "VLM call returned zero observations."
                    ),
                )
            )

        return contradictions

    def build(
        self,
        db: Session,
        session_id: uuid.UUID,
        frame: Frame,
        crowd_metrics: CrowdMetrics,
        risk_state_result: RiskStateResult,
        trigger_decision: TriggerDecision,
        roi_bbox: tuple[float, float, float, float],
        vision_result: VisionAnalysisResult | None,
        vlm_call_succeeded: bool,
    ) -> EvidencePackageResult:
        session = db.get(AnalysisSession, session_id)
        if session is None:
            raise ValueError(f"AnalysisSession {session_id} not found")

        vision_observations = vision_result.observations if vision_result is not None else []

        package_id = uuid.uuid4()

        representative_frame_path, roi_crop_path = self._save_images(
            session_id, package_id, frame, roi_bbox
        )

        compact_metrics = CompactCrowdMetricsSummary(
            risk_score=risk_state_result.risk_score,
            risk_state=risk_state_result.state,
            max_density=float(crowd_metrics.core.density.grid.max()),
            max_pressure=crowd_metrics.core.pressure.max_pressure,
            pressure_units_disclaimer=crowd_metrics.core.pressure.units_disclaimer,
            congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
            reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
            bottleneck_signal_present=crowd_metrics.bottleneck is not None,
            density_confidence=crowd_metrics.core.density.estimation_confidence,
        )

        risk_state_snapshot = RiskStateSnapshot(
            frame_number=risk_state_result.frame_number,
            timestamp_seconds=risk_state_result.timestamp_seconds,
            state=risk_state_result.state.value,
            risk_score=risk_state_result.risk_score,
        )

        confidence, binding_constraint = self._compute_confidence(crowd_metrics, vision_observations)
        missing = self._compute_completeness(crowd_metrics, vlm_call_succeeded)
        contradictions = self._detect_contradictions(
            crowd_metrics, risk_state_result, vision_observations, vlm_call_succeeded
        )

        return EvidencePackageResult(
            package_id=package_id,
            schema_version=SCHEMA_VERSION,
            session_id=session_id,
            # Resolution 6: time window simplified to a SINGLE point — the
            # triggering frame's own frame_number/timestamp_seconds, per
            # TriggerDecision (the object carrying trigger_type/reason for
            # this exact event). Deciding how many frames to bundle around a
            # trigger moment is arguably an orchestration-level decision not
            # yet made.
            frame_number=trigger_decision.frame_number,
            timestamp_seconds=trigger_decision.timestamp_seconds,
            trigger_type=trigger_decision.trigger_type,
            trigger_reason=trigger_decision.reason,
            # Decision #4: reuse the session's EXISTING model_config_id
            # (AnalysisSession, stored since Phase 4) rather than inventing a
            # new provenance mechanism.
            model_config_id=session.model_config_id,
            crowd_metrics_summary=compact_metrics,
            risk_state_snapshot=risk_state_snapshot,
            vision_observations=vision_observations,
            vlm_call_succeeded=vlm_call_succeeded,
            confidence=confidence,
            binding_constraint=binding_constraint,
            complete=len(missing) == 0,
            missing=missing,
            contradictions=contradictions,
            representative_frame_path=representative_frame_path,
            roi_crop_path=roi_crop_path,
            roi_bbox=tuple(roi_bbox),
        )
