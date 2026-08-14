"""Debug CLI (Phase 16): chains the FULL pipeline through Phase 15
(FrameSource -> YOLO11nDetector -> ByteTrackAdapter -> DISOpticalFlowAdapter
-> CrowdMetricsEngine -> RiskStateMachine -> TriggerEngine -> MiniCPMVisionModel,
all constructed ONCE before the loop) against people_clip.mp4. Whenever
TriggerEngine returns non-NONE, calls the REAL MiniCPMVisionModel, then
REAL EvidenceBuilder.build(), then REAL evidence_service persistence —
genuine end-to-end construction, not simulated.

RESOLUTION 2 (cadence is NOT this phase's decision): "build on every
non-NONE trigger" here is a REASONABLE cadence for DEMONSTRATION PURPOSES
ONLY — explicitly NOT a claim about production cadence, same scoping
language already used in Phase 12's heatmap generation preview script.

======================================================================
KNOWN ENVIRONMENT CAVEAT — same class of issue as Phase 13/14/15
======================================================================
VLM_MODEL / RISK_* thresholds are PRE-EXISTING env keys (Phase 1
placeholders). If the developer's real .env still sets them,
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
values (same pattern as Phase 13/14's own preview scripts) to force at
least one real end-to-end trigger -> VLM call -> EvidenceBuilder.build() ->
persistence cycle. The VLM call itself is STILL REAL inference — only the
risk_score/trigger CONDITION is synthetic.

Usage: python scripts/preview_evidence_package.py <video_id>
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
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetrics, CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.evidence_builder import EvidenceBuilder  # noqa: E402
from app.pipeline.minicpm_vlm import MiniCPMVisionModel, VLMUnavailableError  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.risk_score import RiskScoreResult, compute_risk_grid  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.roi_selection import select_roi  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import evidence_service, session_service  # noqa: E402

CONTIGUOUS_FRAME_COUNT = 150


def _print_active_config_diagnostic() -> None:
    live_vlm = settings.VLM_MODEL
    code_vlm_default = Settings.model_fields["VLM_MODEL"].default
    print(f"Active VLM_MODEL={live_vlm!r} (OLLAMA_BASE_URL={settings.OLLAMA_BASE_URL})")
    if live_vlm != code_vlm_default:
        print(
            f"  WARNING: active VLM_MODEL differs from config.py's authored default "
            f"({code_vlm_default!r}) — the developer's real .env still sets this "
            "Phase-1-placeholder key. See DECISIONS.md."
        )
    print(f"EVIDENCE_FRAMES_STORAGE_PATH={settings.EVIDENCE_FRAMES_STORAGE_PATH!r}")


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
    label, db, session_id, builder, vision_model, frame, crowd_metrics, risk_result,
    trigger_decision, roi_bbox, compact_metrics, built_packages, failures, latencies,
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
        latencies.append(vision_result.model_latency_seconds)
    except Exception as exc:
        vlm_call_succeeded = False
        failures.append(str(exc))
        print(f"  [{label}] VLM call FAILED: {exc}")

    package_result = builder.build(
        db=db, session_id=session_id, frame=frame, crowd_metrics=crowd_metrics,
        risk_state_result=risk_result, trigger_decision=trigger_decision, roi_bbox=roi_bbox,
        vision_result=vision_result, vlm_call_succeeded=vlm_call_succeeded,
    )
    persisted = evidence_service.persist_evidence_package(db, package_result)
    built_packages.append(persisted.id)

    print(
        f"  [{label}] package_id={persisted.id} frame={trigger_decision.frame_number} "
        f"trigger_type={trigger_decision.trigger_type.value} reason=\"{trigger_decision.reason}\""
    )
    print(
        f"    confidence={package_result.confidence:.3f} "
        f"binding_constraint={package_result.binding_constraint!r}"
    )
    print(f"    complete={package_result.complete} missing={package_result.missing}")
    if package_result.contradictions:
        for c in package_result.contradictions:
            print(f"    CONTRADICTION: {c.contradiction_type} ({c.resolution_status}) — {c.description}")
    else:
        print("    contradictions: none")
    print(
        f"    representative_frame_path={package_result.representative_frame_path} "
        f"roi_crop_path={package_result.roi_crop_path}"
    )
    return persisted


def main() -> None:
    if len(sys.argv) != 2:
        print("Usage: python scripts/preview_evidence_package.py <video_id>")
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
                        "REAL", db, session.id, builder, vision_model, frame, crowd_metrics,
                        risk_result, trigger_decision, roi_bbox, compact_metrics,
                        built_packages, failures, latencies,
                    )

                last_real_frame = frame
                last_crowd_metrics = crowd_metrics

            frames_processed += 1
            prev_frame = frame

    print()
    print("=== Real-video summary ===")
    print(f"Frames processed: {frames_processed}")
    print(f"Real triggers fired by type: {{{', '.join(f'{t.value}={c}' for t, c in real_triggers.items())}}}")
    print(f"Real evidence packages built: {len(built_packages)}")

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
            "persistence cycle."
        )
        print("=== SYNTHETIC STRESS-TEST ADDENDUM (constructed risk_score, REAL VLM inference) ===")

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
        synthetic_packages = 0

        for stage_name, stage_value in (("ELEVATED", elevated_value), ("CRITICAL", critical_value)):
            for _ in range(stage_frames):
                timestamp_seconds += frame_dt
                risk_score_result = RiskScoreResult(
                    frame_number=frame_number, timestamp_seconds=timestamp_seconds,
                    risk_score=stage_value, confidence=1.0,
                    contributing_signals=["pressure"], sub_scores={"pressure": stage_value},
                )
                # Real CrowdMetrics sub-fields are REUSED from the last real
                # cycle (EvidenceBuilder needs .core/.congestion/.reverse_flow
                # /.bottleneck to be real objects, not None) — only
                # risk_score is synthetic, same "real image, synthetic
                # trigger condition" discipline as Phase 14's preview script.
                synthetic_crowd_metrics = CrowdMetrics(
                    frame_number=frame_number, timestamp_seconds=timestamp_seconds,
                    core=last_crowd_metrics.core, congestion=last_crowd_metrics.congestion,
                    bottleneck=last_crowd_metrics.bottleneck, reverse_flow=last_crowd_metrics.reverse_flow,
                    risk_score=risk_score_result, predictive_projection=None,
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
                        "SYNTHETIC", db, session.id, builder, vision_model, last_real_frame,
                        synthetic_crowd_metrics, risk_result, trigger_decision, roi_bbox,
                        compact_metrics, built_packages, failures, latencies,
                    )
                    synthetic_packages += 1

                frame_number += 1
            print(f"  (stage {stage_name} target reached: final state={risk_result.state.value})")

        print()
        print(f"Synthetic triggers fired: {synthetic_triggers}")
        print(f"Synthetic evidence packages built: {synthetic_packages}")

    # ==================================================================
    # Round-trip verification: re-query persisted data via the service
    # layer directly (GET-equivalent), confirm files genuinely exist.
    # ==================================================================
    print()
    print("=== Persistence round-trip verification ===")
    entries = evidence_service.get_session_evidence_packages(db, session.id)
    print(f"get_session_evidence_packages returned {len(entries)} package(s) for this session")
    storage_dir = builder.get_storage_dir()
    for entry in entries[:5]:
        package = entry.package
        frame_path = storage_dir / package.representative_frame_path
        roi_path = storage_dir / package.roi_crop_path
        single = evidence_service.get_evidence_package(db, package.id)
        print(
            f"  package_id={package.id} confidence={package.confidence:.3f} "
            f"complete={package.complete} items={len(entry.items)} "
            f"single-fetch-matches={single is not None and single.package.id == package.id} "
            f"frame_file_exists={frame_path.exists()} roi_file_exists={roi_path.exists()}"
        )

    print()
    print("=== Final summary ===")
    print(f"Total real evidence packages built: {len(built_packages)}")
    if latencies:
        print(f"Average measured VLM latency across ALL calls: {sum(latencies) / len(latencies):.2f}s "
              f"(min={min(latencies):.2f}s, max={max(latencies):.2f}s, n={len(latencies)})")
    else:
        print("Average measured VLM latency: n/a (no successful VLM calls)")
    if failures:
        print(f"VLM failures encountered ({len(failures)}) — these still produced an incomplete, "
              "but genuinely persisted, EvidencePackage (vlm_call_succeeded=False):")
        for failure in failures:
            print(f"  - {failure}")
    else:
        print("No VLM failures encountered.")

    db.close()


if __name__ == "__main__":
    main()
