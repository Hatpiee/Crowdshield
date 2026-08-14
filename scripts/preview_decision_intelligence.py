"""Debug CLI (Phase 17): chains the FULL pipeline through Phase 16
(FrameSource -> YOLO11nDetector -> ByteTrackAdapter -> DISOpticalFlowAdapter
-> CrowdMetricsEngine -> RiskStateMachine -> TriggerEngine -> MiniCPMVisionModel
-> EvidenceBuilder -> evidence_service, all constructed ONCE before the
loop) against people_clip.mp4, extended with a REAL Reasoner call after
each persisted EvidencePackage — genuine end-to-end construction, not
simulated.

RESOLUTION 2 (cadence is NOT this phase's decision, same as Phase 16):
"reason on every persisted evidence package" here is a REASONABLE cadence
for DEMONSTRATION PURPOSES ONLY.

======================================================================
KNOWN ENVIRONMENT CAVEAT — same class of issue as Phase 13/14/15/16
======================================================================
LLM_MODEL / VLM_MODEL / RISK_* thresholds are PRE-EXISTING env keys (Phase
1 placeholders). If the developer's real .env still sets them,
pydantic-settings' env-file source overrides config.py's new defaults —
this project NEVER edits the developer's real .env. This script prints the
ACTIVELY CONFIGURED values at startup and warns loudly if they differ from
config.py's own authored defaults. See DECISIONS.md.

======================================================================
SYNTHETIC STRESS-TEST ADDENDUM
======================================================================
people_clip.mp4 is an established sparse/low-risk video. If zero triggers
fire on the real footage, this script continues with the SAME
already-constructed instances, feeding artificially elevated risk_score
values (same pattern established since Phase 13) to force at least one
real end-to-end trigger -> VLM -> EvidenceBuilder -> persist -> Reasoner ->
persist cycle. The VLM/LLM calls themselves are STILL REAL inference —
only the risk_score/trigger CONDITION is synthetic.

Usage: python scripts/preview_decision_intelligence.py <video_id>
"""

import sys
from pathlib import Path
from uuid import UUID

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import numpy as np  # noqa: E402

from app.core.config import REPO_ROOT, Settings, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.abstention import should_abstain  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetrics, CrowdMetricsEngine  # noqa: E402
from app.pipeline.decision_result import DecisionOutcome  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.evidence_builder import EvidenceBuilder  # noqa: E402
from app.pipeline.minicpm_vlm import MiniCPMVisionModel, VLMUnavailableError  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.reasoner import LLMUnavailableError, Reasoner  # noqa: E402
from app.pipeline.risk_score import RiskScoreResult, compute_risk_grid  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.roi_selection import select_roi  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import decision_service, evidence_service, session_service  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150


def _print_active_config_diagnostic() -> None:
    for field_name in ("VLM_MODEL", "LLM_MODEL"):
        live = getattr(settings, field_name)
        code_default = Settings.model_fields[field_name].default
        print(f"Active {field_name}={live!r}")
        if live != code_default:
            print(
                f"  WARNING: active {field_name} differs from config.py's authored "
                f"default ({code_default!r}) — the developer's real .env still "
                "sets this Phase-1-placeholder key. See DECISIONS.md."
            )
    print(f"OLLAMA_BASE_URL={settings.OLLAMA_BASE_URL!r} DECISION_CONFIDENCE_FLOOR={settings.DECISION_CONFIDENCE_FLOOR}")


def _compact_metrics(crowd_metrics, risk_score_value, risk_state_value) -> CompactCrowdMetricsSummary:
    return CompactCrowdMetricsSummary(
        risk_score=risk_score_value,
        risk_state=risk_state_value,
        max_density=float(crowd_metrics.core.density.grid.max()),
        max_pressure=crowd_metrics.core.pressure.max_pressure,
        pressure_units_disclaimer=crowd_metrics.core.pressure.units_disclaimer,
        congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
        reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
        bottleneck_signal_present=crowd_metrics.bottleneck is not None,
        density_confidence=crowd_metrics.core.density.estimation_confidence,
    )


def _run_cycle(
    label, db, session_id, builder, vision_model, reasoner, frame, crowd_metrics, risk_result,
    trigger_decision, roi_bbox, compact_metrics, built_packages, built_decisions, failures, latencies,
):
    vision_input = VisionInput(
        representative_frame=frame,
        roi_crop_bbox=roi_bbox,
        compact_metrics=compact_metrics,
        trigger_reason=trigger_decision.reason,
    )
    vlm_call_succeeded = True
    vision_result = None
    try:
        vision_result = vision_model.analyze(vision_input)
        latencies.append(("vlm", vision_result.model_latency_seconds))
    except Exception as exc:
        vlm_call_succeeded = False
        failures.append(f"[{label}] VLM: {exc}")
        print(f"  [{label}] VLM call FAILED: {exc}")

    package_result = builder.build(
        db=db, session_id=session_id, frame=frame, crowd_metrics=crowd_metrics,
        risk_state_result=risk_result, trigger_decision=trigger_decision, roi_bbox=roi_bbox,
        vision_result=vision_result, vlm_call_succeeded=vlm_call_succeeded,
        predictive_projection=crowd_metrics.predictive_projection,
    )
    persisted_package = evidence_service.persist_evidence_package(db, package_result)
    built_packages.append(persisted_package.id)

    print(
        f"  [{label}] package_id={persisted_package.id} frame={trigger_decision.frame_number} "
        f"trigger_type={trigger_decision.trigger_type.value} reason=\"{trigger_decision.reason}\""
    )
    print(f"    evidence confidence={package_result.confidence:.3f} complete={package_result.complete}")

    abstain_reason_preview = should_abstain(package_result)
    print(f"    would_abstain={abstain_reason_preview is not None} ({abstain_reason_preview})")

    try:
        import time
        start = time.perf_counter()
        decision_result = reasoner.reason(package_result)
        latencies.append(("llm", time.perf_counter() - start))
    except LLMUnavailableError as exc:
        failures.append(f"[{label}] LLM: {exc}")
        print(f"  [{label}] Reasoner call FAILED: {exc}")
        return

    persisted_decision = decision_service.persist_decision_result(db, decision_result)
    built_decisions.append(persisted_decision.id)

    print(
        f"    decision_id={persisted_decision.id} outcome={decision_result.outcome.value} "
        f"abstained={decision_result.outcome == DecisionOutcome.ABSTAIN}"
    )
    print(f"    evidence_cited={decision_result.evidence_cited}")
    if decision_result.recommendation is not None:
        print(f"    recommendation={decision_result.recommendation.value} rationale={decision_result.recommendation_rationale!r}")
    else:
        print("    recommendation=None")
    if decision_result.projection_narrative is not None:
        print(f"    projection_narrative={decision_result.projection_narrative!r}")


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_decision_intelligence.py <video_id>")
        sys.exit(1)

    video_id = UUID(sys.argv[1])
    db = SessionLocal()

    video = db.query(VideoAsset).filter(VideoAsset.id == video_id).first()
    if video is None:
        print(f"No video found with id={video_id}")
        sys.exit(1)
    storage_path = REPO_ROOT / settings.VIDEO_STORAGE_PATH / video.storage_filename
    fps = video.fps
    frame_width = video.width
    frame_height = video.height

    if not storage_path.exists():
        print(f"Video file not found on disk: {storage_path}")
        sys.exit(1)
    if not fps or fps <= 0 or not frame_width or not frame_height:
        print("Video is missing required metadata (fps/width/height).")
        sys.exit(1)

    user = db.query(User).first()
    if user is None:
        print("No user exists in the database — cannot create a real AnalysisSession.")
        sys.exit(1)

    session = session_service.create_session(db, video.id, user.id)
    print(f"Created REAL AnalysisSession id={session.id} (creator={user.email})")
    print(f"Video: {video.original_filename} ({storage_path}), fps={fps}, {frame_width}x{frame_height}")
    _print_active_config_diagnostic()

    try:
        vision_model = MiniCPMVisionModel()
    except VLMUnavailableError as exc:
        print(f"FATAL: MiniCPMVisionModel unavailable: {exc}")
        db.close()
        sys.exit(1)

    try:
        reasoner = Reasoner()
    except LLMUnavailableError as exc:
        print(f"FATAL: Reasoner unavailable: {exc}")
        db.close()
        sys.exit(1)

    detector = YOLO11nDetector()
    tracker = ByteTrackAdapter(fps=fps)
    optical_flow = DISOpticalFlowAdapter()
    engine = CrowdMetricsEngine(frame_width=frame_width, frame_height=frame_height)
    risk_machine = RiskStateMachine()
    trigger_engine = TriggerEngine()
    builder = EvidenceBuilder()
    crowd_grid = CrowdGrid.from_frame_dimensions(frame_width, frame_height)

    real_triggers = {t: 0 for t in TriggerType}
    built_packages = []
    built_decisions = []
    latencies = []
    failures = []
    last_real_frame = None
    last_crowd_metrics = None

    print()
    print("=== REAL VIDEO (continuous, every frame) ===")
    prev_frame = None
    frames_processed = 0
    with MP4FrameSource(storage_path, frame_step=1) as source:
        for frame in source.frames():
            if frames_processed >= CONTIGUOUS_FRAME_COUNT:
                break

            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = engine.update(tracking_result, motion_result, elapsed_seconds)

                risk_result = risk_machine.update(crowd_metrics)
                trigger_decision = trigger_engine.evaluate(crowd_metrics, risk_result)

                if trigger_decision.trigger_type != TriggerType.NONE:
                    real_triggers[trigger_decision.trigger_type] += 1

                    risk_grid = compute_risk_grid(
                        crowd_metrics.core.pressure, crowd_metrics.congestion,
                        crowd_metrics.bottleneck, crowd_metrics.reverse_flow,
                    )
                    roi_bbox = select_roi(risk_grid, crowd_grid, frame_width, frame_height)
                    compact_metrics = _compact_metrics(crowd_metrics, risk_result.risk_score, risk_result.state)

                    _run_cycle(
                        "REAL", db, session.id, builder, vision_model, reasoner, frame, crowd_metrics,
                        risk_result, trigger_decision, roi_bbox, compact_metrics,
                        built_packages, built_decisions, failures, latencies,
                    )

                last_real_frame = frame
                last_crowd_metrics = crowd_metrics

            frames_processed += 1
            prev_frame = frame

    print()
    print("=== Real-video summary ===")
    print(f"Frames processed: {frames_processed}")
    print(f"Real triggers fired by type: {{{', '.join(f'{t.value}={c}' for t, c in real_triggers.items())}}}")
    print(f"Real evidence packages built: {len(built_packages)}, real decisions built: {len(built_decisions)}")

    total_real_triggers = sum(c for t, c in real_triggers.items() if t != TriggerType.NONE)

    # ==================================================================
    # SYNTHETIC STRESS-TEST ADDENDUM — forces a trigger if none fired
    # ==================================================================
    if total_real_triggers == 0:
        print()
        print(
            "No real triggers fired — given people_clip.mp4's established "
            "sparse/low-risk profile, this is PLAUSIBLE, HONEST behavior, "
            "not a bug. Running a SYNTHETIC stress-test addendum to still "
            "exercise a real end-to-end trigger -> VLM -> EvidenceBuilder -> "
            "persist -> Reasoner -> persist cycle."
        )
        print("=== SYNTHETIC STRESS-TEST ADDENDUM (constructed risk_score, REAL VLM+LLM inference) ===")

        if last_real_frame is None or last_crowd_metrics is None:
            print("FATAL: no real frame was ever captured to use as the VLM image — aborting.")
            db.close()
            sys.exit(1)

        rise_elevated = settings.RISK_ELEVATED_THRESHOLD
        rise_critical = settings.RISK_CRITICAL_THRESHOLD
        gap = rise_critical - rise_elevated
        elevated_value = rise_elevated + (gap / 2.0)
        critical_value = rise_critical + gap + 1.0
        persistence_frames = settings.RISK_STATE_PERSISTENCE_FRAMES
        stage_frames = persistence_frames + 5

        synthetic_grid = np.zeros((crowd_grid.rows, crowd_grid.cols))
        hot_row, hot_col = crowd_grid.rows // 2, crowd_grid.cols // 2

        frame_number = last_real_frame.frame_number + 1
        timestamp_seconds = last_real_frame.timestamp_seconds
        frame_dt = 1.0 / float(fps)
        synthetic_triggers = 0
        synthetic_cycles = 0

        for stage_name, stage_value in (("ELEVATED", elevated_value), ("CRITICAL", critical_value)):
            for _ in range(stage_frames):
                timestamp_seconds += frame_dt
                # Phase 17 needs at least one COMPLETE synthetic cycle (all
                # four sub-signals contributing) to prove a real,
                # non-abstained, LLM-produced decision end-to-end — unlike
                # Phase 16's own script, which only needed a trigger to
                # fire and didn't care about should_abstain()'s
                # completeness check.
                risk_score_result = RiskScoreResult(
                    frame_number=frame_number, timestamp_seconds=timestamp_seconds,
                    risk_score=stage_value, confidence=1.0,
                    contributing_signals=["pressure", "congestion", "bottleneck", "reverse_flow"],
                    sub_scores={
                        "pressure": stage_value, "congestion": stage_value,
                        "bottleneck": stage_value, "reverse_flow": stage_value,
                    },
                )
                synthetic_crowd_metrics = CrowdMetrics(
                    frame_number=frame_number, timestamp_seconds=timestamp_seconds,
                    core=last_crowd_metrics.core, congestion=last_crowd_metrics.congestion,
                    bottleneck=last_crowd_metrics.bottleneck, reverse_flow=last_crowd_metrics.reverse_flow,
                    risk_score=risk_score_result, predictive_projection=last_crowd_metrics.predictive_projection,
                )
                risk_result = risk_machine.update(synthetic_crowd_metrics)
                trigger_decision = trigger_engine.evaluate(synthetic_crowd_metrics, risk_result)

                if trigger_decision.trigger_type != TriggerType.NONE:
                    synthetic_triggers += 1
                    synthetic_grid[hot_row, hot_col] = stage_value
                    roi_bbox = select_roi(synthetic_grid, crowd_grid, frame_width, frame_height)
                    compact_metrics = CompactCrowdMetricsSummary(
                        risk_score=stage_value, risk_state=risk_result.state,
                        max_density=0.0, max_pressure=0.0,
                        pressure_units_disclaimer="PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS (SYNTHETIC)",
                        congested_cell_fraction=0.0, reverse_flow_cell_fraction=0.0,
                        bottleneck_signal_present=False, density_confidence=1.0,
                    )

                    _run_cycle(
                        "SYNTHETIC", db, session.id, builder, vision_model, reasoner, last_real_frame,
                        synthetic_crowd_metrics, risk_result, trigger_decision, roi_bbox,
                        compact_metrics, built_packages, built_decisions, failures, latencies,
                    )
                    synthetic_cycles += 1

                frame_number += 1
            print(f"  (stage {stage_name} target reached: final state={risk_result.state.value})")

        print()
        print(f"Synthetic triggers fired: {synthetic_triggers}")
        print(f"Synthetic evidence+decision cycles built: {synthetic_cycles}")

    # ==================================================================
    # Round-trip verification
    # ==================================================================
    print()
    print("=== Persistence round-trip verification ===")
    decision_rows = decision_service.get_session_decisions(db, session.id)
    print(f"get_session_decisions returned {len(decision_rows)} decision(s) for this session")
    for row in decision_rows[:5]:
        single = decision_service.get_decision_result(db, row.id)
        print(
            f"  decision_id={row.id} outcome={row.outcome.value} confidence={row.confidence:.3f} "
            f"recommendation={row.recommendation.value if row.recommendation else None} "
            f"single-fetch-matches={single is not None and single.id == row.id}"
        )

    print()
    print("=== Final summary ===")
    print(f"Total real evidence packages built: {len(built_packages)}, decisions built: {len(built_decisions)}")
    abstained_count = sum(1 for row in decision_rows if row.outcome == DecisionOutcome.ABSTAIN)
    non_abstained_count = len(decision_rows) - abstained_count
    print(f"Abstained: {abstained_count}, non-abstained (real LLM-produced): {non_abstained_count}")
    vlm_latencies = [v for k, v in latencies if k == "vlm"]
    llm_latencies = [v for k, v in latencies if k == "llm"]
    if vlm_latencies:
        print(f"Average VLM latency: {sum(vlm_latencies) / len(vlm_latencies):.2f}s (n={len(vlm_latencies)})")
    if llm_latencies:
        print(f"Average LLM (Reasoner) latency: {sum(llm_latencies) / len(llm_latencies):.2f}s (n={len(llm_latencies)})")
    if failures:
        print(f"Failures encountered ({len(failures)}):")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print("No failures encountered.")

    db.close()


if __name__ == "__main__":
    main()
