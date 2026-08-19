"""Acute-Hazard Validation Harness (Validation Harness + Real-Footage
Readiness phase). See DECISIONS.md, "Acute-Hazard Validation Harness Phase"
for the full rationale.

============================================================
WHAT THIS IS AND IS NOT
============================================================
This is a DIAGNOSTIC/VALIDATION TOOL, not a change to the production
detector. It makes ARBITRARY real video files easy to run through the
existing, UNMODIFIED CrowdShield pipeline (AcuteHazardDetector /
TriggerEngine / EvidenceBuilder / abstention / Reasoner / Verifier /
incident_service — every one of these is imported and called exactly as
production does, never reimplemented) and produces a human-reviewable
package of artifacts for every ACUTE_HAZARD trigger: the exact frames
around the event, the deterministic signal snapshot that fired it, the
five heatmaps at the nearest event timestamp, the VLM/Reasoner/Verifier/
Incident outcome (when the full semantic chain is run), and a plain-
language operator interpretation that never hides uncertainty.

No ACUTE_HAZARD_* threshold, quorum rule, spatial filter, persistence
filter, or contradiction check is touched by this script. No video
filename is special-cased anywhere below.

============================================================
TWO MODES
============================================================
1. Deterministic-only scan (default). Runs YOLO/ByteTrack/DIS-optical-flow/
   CrowdMetricsEngine/AcuteHazardDetector/TriggerEngine directly over the
   video's frames (same composition as scripts/calibrate_acute_hazard_
   false_positives.py) — NO database writes, NO VLM call, NO LLM call. Fast,
   safe to run against many candidate videos to see which ones produce any
   ACUTE_HAZARD trigger at all before spending real Ollama time on them.

2. `--full-chain`. Registers the video as a real VideoAsset, creates a real
   AnalysisSession, and calls the REAL, UNMODIFIED
   `AnalysisOrchestrator(session_id).run()` entry point — the exact code
   path a real upload + "start analysis" API call takes. This is the ONLY
   mode that actually exercises the VLM/EvidenceBuilder/abstention/
   Reasoner/Verifier/incident_service chain. The VLM is still only ever
   called when ACUTE_HAZARD (or another trigger) actually fires — this
   script does not add, and could not add without editing
   analysis_orchestrator.py itself (which it does not touch), an always-on
   VLM benchmark.

============================================================
SINGLE-VIDEO vs BATCH
============================================================
`python scripts/validate_acute_event_video.py <video_path> [options]`
  validates ONE video.

`python scripts/validate_acute_event_video.py --manifest <manifest.json> [options]`
  validates every entry in a manifest (see MANIFEST FORMAT below) and
  additionally writes an aggregate report. Duplicate videos (identical
  SHA256) are detected and reported, never silently treated as two
  independent samples.

============================================================
MANIFEST FORMAT
============================================================
A JSON file containing a list of objects. Only `sample_id` and `path` are
required; every other field may be omitted (stays `null`/unknown, never
guessed):
{
  "sample_id": "explosion_01",
  "path": "C:/videos/explosion_01.mp4",
  "event_type": "EXPLOSIVE_EVENT",          # free text, informational only
  "event_start": null,
  "event_peak": null,
  "event_end": null,
  "camera_motion": "static",
  "crowd_density_class": "medium",
  "source_notes": "...",
  "ground_truth_status": "positive_acute_event",  # or "negative_control", or omitted
  "reviewer_notes": null
}
`ground_truth_status` is the ONLY field this script interprets (to decide
whether an eventual accuracy/precision claim is even possible — see
`_NO_GROUND_TRUTH`). Every other field is carried through into the output
verbatim for a human reviewer; this script never infers or fabricates any
of them.

============================================================
OUTPUT
============================================================
Every run (single-video or one manifest entry inside a batch) writes to
`<output_dir>/<run_id>/`:
  run_metadata.json   - Step 2 fields: sha256, duration, fps, dimensions,
                         frame count, selected window, active model names,
                         ACUTE_HAZARD config, run mode.
  results.json         - full machine-readable results (stable schema).
  summary.md            - human-readable run summary + links to each event.
  events/event_NNNN/    - one directory per ACUTE_HAZARD trigger:
    report.md            - Event / Deterministic evidence / VLM evidence /
                            Decision layer / Operator interpretation.
    montage.jpg           - contact sheet (before/trigger/after/ROI/heatmaps).
    before.jpg, trigger.jpg, after.jpg, roi.jpg
    heatmap_<type>.jpg     - one per heatmap type available at this event.
    evidence_package.json, decision_result.json, incident.json
                          - present only in --full-chain mode.

Batch mode additionally writes `<output_dir>/<run_id>/aggregate.json` and
`aggregate.md` at the batch's own run_id (one level above the per-sample
run directories).
"""

import argparse
import hashlib
import json
import shutil
import sys
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.decision import DecisionResultRow  # noqa: E402
from app.models.evidence import EvidenceItem, EvidencePackage  # noqa: E402
from app.models.heatmap import HeatmapSnapshot, HeatmapType  # noqa: E402
from app.models.incident import Incident, IncidentEvidence  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.acute_hazard_detector import AcuteHazardDetector, SIGNAL_ORDER  # noqa: E402
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetrics, CrowdMetricsEngine  # noqa: E402
from app.pipeline.decision_result import DecisionOutcome  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.heatmap_rendering import (  # noqa: E402
    render_density_heatmap,
    render_flow_congestion_heatmap,
    render_predictive_heatmap,
    render_pressure_heatmap,
    render_risk_heatmap,
)
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.roi_selection import select_roi  # noqa: E402
from app.pipeline.trigger_engine import TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import session_service  # noqa: E402

_NO_GROUND_TRUTH = "NO_GROUND_TRUTH"

# Step 3/5: bounded before/after context — reuses the SAME lookback duration
# production already uses for an ACUTE_HAZARD trigger's own "before" VLM
# context frame (settings.ACUTE_HAZARD_CONTEXT_FRAME_LOOKBACK_SECONDS), and
# applies the identical duration symmetrically forward for "after" (no
# production precedent for an "after" frame exists — this is a validation-
# harness-only diagnostic choice, not a production behavior).
_CONTEXT_LOOKBACK_SECONDS = settings.ACUTE_HAZARD_CONTEXT_FRAME_LOOKBACK_SECONDS

_JPEG_QUALITY = 92
_MONTAGE_PANEL_WIDTH = 320
_MONTAGE_PANEL_HEIGHT = 200
_MONTAGE_COLUMNS = 4
_MONTAGE_FONT = cv2.FONT_HERSHEY_SIMPLEX

_HEATMAP_ORDER = [
    HeatmapType.DENSITY, HeatmapType.PRESSURE, HeatmapType.FLOW_CONGESTION,
    HeatmapType.RISK, HeatmapType.PREDICTIVE,
]


# ============================================================
# Small, generic utilities (Step 6: SHA256 identity/duplicate detection)
# ============================================================

def compute_sha256(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def make_run_id(prefix: str = "") -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    short = uuid.uuid4().hex[:8]
    return f"{prefix}{stamp}_{short}" if prefix else f"{stamp}_{short}"


def _save_jpeg(path: Path, image: np.ndarray) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, _JPEG_QUALITY])
    if not success:
        raise RuntimeError(f"Failed to JPEG-encode image for {path}")
    path.write_bytes(encoded.tobytes())


def _extract_frame_at(video_path: Path, frame_number: int) -> Optional[np.ndarray]:
    """Independent, read-only re-open of the same video file to pull a
    SPECIFIC frame by index — used only for before/after diagnostic
    context, never fed back into any deterministic/semantic decision.
    Returns None if the frame index is out of range (e.g. an event too
    close to the very start/end of the video)."""
    capture = cv2.VideoCapture(str(video_path))
    try:
        if not capture.isOpened():
            return None
        capture.set(cv2.CAP_PROP_POS_FRAMES, max(0, frame_number))
        ok, image = capture.read()
        return image if ok else None
    finally:
        capture.release()


def _trim_video(
    source_path: Path, destination_path: Path,
    start_time: Optional[float], end_time: Optional[float], max_duration: Optional[float],
) -> Path:
    """Writes a re-encoded clip covering [start_time, effective_end] of
    source_path to destination_path, returning destination_path. Returns
    source_path UNCHANGED (no trimming, no re-encode) when none of
    start_time/end_time/max_duration were requested — the common case."""
    if start_time is None and end_time is None and max_duration is None:
        return source_path

    with MP4FrameSource(source_path, frame_step=1) as source:
        metadata = source.get_metadata()
        fps = metadata.fps
        effective_start = max(0.0, start_time or 0.0)
        effective_end = end_time if end_time is not None else metadata.duration_seconds
        if max_duration is not None:
            effective_end = min(effective_end, effective_start + max_duration)

        writer = None
        try:
            for frame in source.frames():
                if frame.timestamp_seconds < effective_start:
                    continue
                if frame.timestamp_seconds > effective_end:
                    break
                if writer is None:
                    destination_path.parent.mkdir(parents=True, exist_ok=True)
                    fourcc = cv2.VideoWriter_fourcc(*"mp4v")
                    writer = cv2.VideoWriter(
                        str(destination_path), fourcc, fps, (frame.width, frame.height)
                    )
                writer.write(frame.image)
        finally:
            if writer is not None:
                writer.release()

    if writer is None:
        raise ValueError(
            f"start_time={start_time}/end_time={end_time}/max_duration={max_duration} "
            f"selected zero frames from {source_path}"
        )
    return destination_path


def _spatial_coherence(localization_grid: np.ndarray) -> tuple[float, float, int]:
    """Same diagnostic as scripts/calibrate_acute_hazard_false_positives.py's
    own helper — returns (active_cell_fraction, largest_component_fraction,
    num_active_cells). Purely diagnostic, never used to gate anything."""
    grid = localization_grid.astype(np.float64)
    mean, std = grid.mean(), grid.std()
    active_mask = grid > (mean + 2.0 * std)
    num_active = int(active_mask.sum())
    active_fraction = num_active / grid.size if grid.size > 0 else 0.0
    if num_active == 0:
        return active_fraction, 0.0, num_active

    rows, cols = active_mask.shape
    visited = np.zeros_like(active_mask, dtype=bool)
    largest = 0
    for r in range(rows):
        for c in range(cols):
            if active_mask[r, c] and not visited[r, c]:
                stack = [(r, c)]
                visited[r, c] = True
                size = 0
                while stack:
                    cr, cc = stack.pop()
                    size += 1
                    for dr, dc in ((1, 0), (-1, 0), (0, 1), (0, -1)):
                        nr, nc = cr + dr, cc + dc
                        if 0 <= nr < rows and 0 <= nc < cols and active_mask[nr, nc] and not visited[nr, nc]:
                            visited[nr, nc] = True
                            stack.append((nr, nc))
                largest = max(largest, size)
    return active_fraction, largest / num_active, num_active


def _render_all_heatmaps(crowd_metrics: CrowdMetrics, frame_width: int, frame_height: int, source_image: np.ndarray, timestamp_seconds: float) -> dict[str, np.ndarray]:
    """Directly renders the 5 heatmap types via the UNMODIFIED
    heatmap_rendering.py functions — used only in deterministic-scan mode,
    where there is no AnalysisSession/DB for heatmap_service's own periodic
    persistence to have already produced snapshots."""
    images: dict[str, np.ndarray] = {}
    images["DENSITY"] = render_density_heatmap(
        crowd_metrics.core.density, frame_width, frame_height, source_image, timestamp_seconds
    )
    images["PRESSURE"] = render_pressure_heatmap(
        crowd_metrics.core.pressure, frame_width, frame_height, source_image, timestamp_seconds
    )
    images["FLOW_CONGESTION"] = render_flow_congestion_heatmap(
        crowd_metrics.congestion, crowd_metrics.core.flow, frame_width, frame_height, source_image, timestamp_seconds
    )
    images["RISK"] = render_risk_heatmap(
        crowd_metrics.core.pressure, crowd_metrics.congestion, crowd_metrics.bottleneck,
        crowd_metrics.reverse_flow, frame_width, frame_height, source_image, timestamp_seconds,
    )
    if crowd_metrics.predictive_projection is not None:
        images["PREDICTIVE"] = render_predictive_heatmap(
            crowd_metrics.core.pressure, crowd_metrics.predictive_projection,
            frame_width, frame_height, source_image, timestamp_seconds,
        )
    return images


def _build_montage(panels: list[tuple[str, Optional[np.ndarray]]], title: str) -> np.ndarray:
    """Step 5: one contact sheet per trigger. `panels` is an ordered list of
    (label, image_or_None) — a None image renders as a plain "NOT AVAILABLE"
    placeholder panel rather than silently shrinking the grid, so a reviewer
    always sees explicitly what is missing and why."""
    cells = []
    for label, image in panels:
        canvas = np.zeros((_MONTAGE_PANEL_HEIGHT, _MONTAGE_PANEL_WIDTH, 3), dtype=np.uint8)
        if image is not None:
            h, w = image.shape[:2]
            scale = min(_MONTAGE_PANEL_WIDTH / w, (_MONTAGE_PANEL_HEIGHT - 24) / h)
            resized = cv2.resize(image, (max(1, int(w * scale)), max(1, int(h * scale))))
            rh, rw = resized.shape[:2]
            y_off = (_MONTAGE_PANEL_HEIGHT - 24 - rh) // 2
            x_off = (_MONTAGE_PANEL_WIDTH - rw) // 2
            canvas[y_off:y_off + rh, x_off:x_off + rw] = resized
        else:
            cv2.putText(canvas, "NOT AVAILABLE", (20, _MONTAGE_PANEL_HEIGHT // 2), _MONTAGE_FONT, 0.5, (0, 0, 200), 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (0, _MONTAGE_PANEL_HEIGHT - 24), (_MONTAGE_PANEL_WIDTH, _MONTAGE_PANEL_HEIGHT), (40, 40, 40), -1)
        cv2.putText(canvas, label, (6, _MONTAGE_PANEL_HEIGHT - 7), _MONTAGE_FONT, 0.5, (255, 255, 255), 1, cv2.LINE_AA)
        cv2.rectangle(canvas, (0, 0), (_MONTAGE_PANEL_WIDTH - 1, _MONTAGE_PANEL_HEIGHT - 1), (90, 90, 90), 1)
        cells.append(canvas)

    columns = min(_MONTAGE_COLUMNS, len(cells)) or 1
    rows = -(-len(cells) // columns)
    grid = np.zeros((rows * _MONTAGE_PANEL_HEIGHT, columns * _MONTAGE_PANEL_WIDTH, 3), dtype=np.uint8)
    for idx, cell in enumerate(cells):
        r, c = divmod(idx, columns)
        grid[r * _MONTAGE_PANEL_HEIGHT:(r + 1) * _MONTAGE_PANEL_HEIGHT, c * _MONTAGE_PANEL_WIDTH:(c + 1) * _MONTAGE_PANEL_WIDTH] = cell

    title_bar = np.zeros((32, grid.shape[1], 3), dtype=np.uint8)
    cv2.putText(title_bar, title, (8, 22), _MONTAGE_FONT, 0.6, (255, 255, 255), 1, cv2.LINE_AA)
    return np.vstack([title_bar, grid])


# ============================================================
# Step 9: deterministic failure-layer diagnosis (full-chain mode only —
# every value below is read directly from real persisted rows, never
# guessed or inferred beyond what those rows actually say)
# ============================================================

def _diagnose_stage(pkg: EvidencePackage, decision: Optional[DecisionResultRow], incident_exists: bool) -> tuple[str, str]:
    if not pkg.vision_observations_present:
        return (
            "VLM_UNAVAILABLE",
            "The VLM call itself failed/was unavailable for this trigger — "
            "EvidencePackage.vision_observations_present=False, "
            "missing_evidence includes 'vision_observations'. No semantic "
            "interpretation occurred; this is an availability issue, not a "
            "detector or evidence-quality issue.",
        )
    if decision is None:
        return (
            "REASONER_UNAVAILABLE",
            "The EvidencePackage was built successfully (the VLM call "
            "succeeded), but no DecisionResult row exists for it — the "
            "Reasoner (LLM) call did not complete (see backend logs around "
            "this timestamp for LLMUnavailableError).",
        )
    if decision.outcome == DecisionOutcome.ABSTAIN:
        reason = decision.abstention_reason or ""
        if "acute-hazard-consistent" in reason:
            layer = "EVIDENCE_CONSISTENCY_GATE"
        elif "contradiction" in reason:
            layer = "CONTRADICTION_CHECK"
        elif "DECISION_CONFIDENCE_FLOOR" in reason:
            layer = "CONFIDENCE_FLOOR"
        elif "incomplete" in reason:
            layer = "INCOMPLETE_EVIDENCE"
        else:
            layer = "ABSTENTION_OTHER"
        return (layer, f"Deterministic abstention (no LLM outcome was reached): {reason}")
    if decision.outcome == DecisionOutcome.NO_INCIDENT:
        return ("REASONER_NO_INCIDENT", "The Reasoner (LLM) reviewed the real evidence and concluded NO_INCIDENT.")
    if decision.outcome == DecisionOutcome.WATCH:
        return ("REASONER_WATCH", "The Reasoner (LLM) reviewed the real evidence and concluded WATCH — worth continued monitoring, not yet incident-worthy.")
    if decision.outcome == DecisionOutcome.INCIDENT:
        if incident_exists:
            return ("INCIDENT_CREATED", "The Reasoner concluded INCIDENT and a real Incident row was created by incident_service.")
        return (
            "INCIDENT_WITHOUT_ROW",
            "The Reasoner concluded INCIDENT but no Incident row exists for "
            "this session — check for a newer, superseding ABSTAIN decision "
            "(Verifier rejection) referencing this decision_id via "
            "superseded_decision_id.",
        )
    return ("UNKNOWN", "Unrecognized decision state.")


# ============================================================
# Event artifact schema
# ============================================================

@dataclass
class EventArtifact:
    event_index: int
    frame_number: int
    timestamp_seconds: float
    trigger_reason: str
    corroborating_signals: list[str]
    z_scores: dict[str, float]
    raw_values: dict[str, float]
    # None in full-chain mode: the localization_grid used to compute this is
    # an in-memory-only field of AcuteHazardSignal, never persisted to the
    # EvidencePackage row — genuinely unavailable after the fact, not
    # fabricated as 0.0.
    spatial_active_cell_fraction: Optional[float]
    spatial_largest_component_fraction: Optional[float]
    roi_bbox: tuple[float, float, float, float]
    heatmap_types_available: list[str] = field(default_factory=list)
    before_frame_available: bool = False
    after_frame_available: bool = False
    # Full-chain-only fields (None in deterministic-scan mode):
    vlm_call_succeeded: Optional[bool] = None
    vision_observation_categories: Optional[list[str]] = None
    evidence_complete: Optional[bool] = None
    evidence_missing: Optional[list[str]] = None
    contradiction_types: Optional[list[str]] = None
    decision_outcome: Optional[str] = None
    abstention_reason: Optional[str] = None
    event_classification: Optional[str] = None
    incident_created: Optional[bool] = None
    diagnosis_stage: Optional[str] = None
    diagnosis_explanation: Optional[str] = None


def _operator_interpretation(artifact: EventArtifact, mode: str) -> str:
    lines = [
        f"The deterministic AcuteHazardDetector flagged frame {artifact.frame_number} "
        f"(t={artifact.timestamp_seconds:.2f}s) because {len(artifact.corroborating_signals)} "
        f"signal(s) corroborated together: {', '.join(artifact.corroborating_signals)}.",
    ]
    if mode != "full-chain":
        lines.append(
            "This run used the deterministic-only scan mode — the VLM/Reasoner/"
            "Verifier/Incident chain was NOT invoked, so whether this would "
            "become an incident is UNKNOWN from this run alone. Re-run with "
            "--full-chain to find out."
        )
        return " ".join(lines)

    if artifact.vlm_call_succeeded is False:
        lines.append("The VLM call failed, so no semantic interpretation of this moment exists.")
    elif artifact.vision_observation_categories is not None:
        if artifact.vision_observation_categories:
            lines.append(f"The VLM reported observation categories: {artifact.vision_observation_categories}.")
        else:
            lines.append("The VLM call succeeded but returned zero observations for this frame.")

    if artifact.decision_outcome == "ABSTAIN":
        lines.append(f"The system abstained deterministically: {artifact.abstention_reason}")
    elif artifact.decision_outcome in ("WATCH", "NO_INCIDENT"):
        lines.append(f"The Reasoner (LLM) concluded {artifact.decision_outcome} — see decision_result.json for its reasoning.")
    elif artifact.decision_outcome == "INCIDENT":
        if artifact.incident_created:
            lines.append(f"The Reasoner concluded INCIDENT (classified {artifact.event_classification}) and a real Incident record was created.")
        else:
            lines.append("The Reasoner concluded INCIDENT but no Incident record exists — see diagnosis_stage.")
    lines.append(
        "What remains uncertain: this reflects only what THIS video's pixels "
        "produced through the current, unmodified pipeline — it is not a "
        "judgment about whether the underlying real-world event was actually "
        "hazardous, and it is not validated against any labeled ground truth "
        "unless this sample's manifest entry explicitly supplies one."
    )
    return " ".join(lines)


def _write_event_report(path: Path, artifact: EventArtifact, mode: str) -> None:
    lines = [
        f"# Event {artifact.event_index:04d}",
        "",
        "## Event",
        f"- timestamp: {artifact.timestamp_seconds:.2f}s",
        f"- frame_number: {artifact.frame_number}",
        f"- trigger_id: ACUTE_HAZARD-{artifact.event_index:04d}",
        f"- trigger_reason: {artifact.trigger_reason}",
        "",
        "## Deterministic evidence",
        f"- corroborating_signals: {artifact.corroborating_signals}",
        f"- z_scores: {json.dumps(artifact.z_scores, indent=2)}",
        f"- raw_values: {json.dumps(artifact.raw_values, indent=2)}",
        "- spatial_active_cell_fraction: " + (
            f"{artifact.spatial_active_cell_fraction:.4f}" if artifact.spatial_active_cell_fraction is not None
            else "N/A (localization_grid is not persisted, unavailable in full-chain mode)"
        ),
        "- spatial_largest_component_fraction: " + (
            f"{artifact.spatial_largest_component_fraction:.4f}" if artifact.spatial_largest_component_fraction is not None
            else "N/A (localization_grid is not persisted, unavailable in full-chain mode)"
        ),
        f"- roi_bbox (pixel space): {artifact.roi_bbox}",
        "",
        "## VLM evidence",
    ]
    if mode != "full-chain":
        lines.append("_Not run this pass (deterministic-only scan mode) — see run_metadata.json's `mode`._")
    else:
        lines.append(f"- vlm_call_succeeded: {artifact.vlm_call_succeeded}")
        lines.append(f"- observation_categories: {artifact.vision_observation_categories}")
        lines.append(f"- evidence complete: {artifact.evidence_complete} (missing: {artifact.evidence_missing})")
        lines.append(f"- contradictions: {artifact.contradiction_types}")
        lines.append("- exact images sent and full observation detail: see evidence_package.json")

    lines += ["", "## Decision layer"]
    if mode != "full-chain":
        lines.append("_Not run this pass (deterministic-only scan mode)._")
    else:
        lines.append(f"- outcome: {artifact.decision_outcome}")
        if artifact.abstention_reason:
            lines.append(f"- abstention_reason: {artifact.abstention_reason}")
        lines.append(f"- event_classification: {artifact.event_classification}")
        lines.append(f"- incident_created: {artifact.incident_created}")
        lines.append(f"- diagnosis_stage: {artifact.diagnosis_stage} — {artifact.diagnosis_explanation}")
        lines.append("- full Reasoner output: see decision_result.json")

    lines += ["", "## Operator interpretation", _operator_interpretation(artifact, mode)]
    lines += ["", "## Artifacts", "- montage.jpg", "- before.jpg / trigger.jpg / after.jpg / roi.jpg"]
    lines += [f"- heatmap_{t.lower()}.jpg" for t in artifact.heatmap_types_available]

    path.write_text("\n".join(lines), encoding="utf-8")


# ============================================================
# Deterministic-only scan mode
# ============================================================

def _run_deterministic_scan(
    video_path: Path, events_dir: Path, sample_interval: int,
    start_time: Optional[float], end_time: Optional[float],
) -> list[EventArtifact]:
    with MP4FrameSource(video_path, frame_step=1) as source:
        metadata = source.get_metadata()
        fps, width, height = metadata.fps, metadata.width, metadata.height

        detector = YOLO11nDetector()
        tracker = ByteTrackAdapter(fps=fps)
        optical_flow = DISOpticalFlowAdapter()
        engine = CrowdMetricsEngine(frame_width=width, frame_height=height)
        crowd_grid = CrowdGrid.from_frame_dimensions(width, height)
        acute_detector = AcuteHazardDetector(crowd_grid)
        risk_machine = RiskStateMachine()
        trigger_engine = TriggerEngine()

        events: list[EventArtifact] = []
        prev_frame = None
        frame_index = 0
        for frame in source.frames():
            if start_time is not None and frame.timestamp_seconds < start_time:
                prev_frame = frame
                continue
            if end_time is not None and frame.timestamp_seconds > end_time:
                break
            if frame_index % sample_interval != 0:
                frame_index += 1
                continue
            frame_index += 1

            detection_result = detector.detect(frame)
            tracking_result = tracker.update(detection_result)

            if prev_frame is not None:
                motion_result = optical_flow.compute(prev_frame, frame)
                elapsed = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = engine.update(tracking_result, motion_result, elapsed)
                acute_signal = acute_detector.update(motion_result, crowd_metrics.core.flow, detection_result, prev_frame, frame)
                risk_result = risk_machine.update(crowd_metrics)
                trigger_decision = trigger_engine.evaluate(crowd_metrics, risk_result, acute_hazard_signal=acute_signal)

                if trigger_decision.trigger_type == TriggerType.ACUTE_HAZARD:
                    event_index = len(events) + 1
                    event_dir = events_dir / f"event_{event_index:04d}"

                    roi_bbox = select_roi(acute_signal.localization_grid, crowd_grid, width, height)
                    active_frac, largest_frac, _ = _spatial_coherence(acute_signal.localization_grid)

                    _save_jpeg(event_dir / "trigger.jpg", frame.image)
                    x_min, y_min, x_max, y_max = (int(round(v)) for v in roi_bbox)
                    roi_crop = frame.image[max(0, y_min):max(1, y_max), max(0, x_min):max(1, x_max)]
                    if roi_crop.size > 0:
                        _save_jpeg(event_dir / "roi.jpg", roi_crop)

                    before_frame_number = max(0, frame.frame_number - round(fps * _CONTEXT_LOOKBACK_SECONDS))
                    after_frame_number = frame.frame_number + round(fps * _CONTEXT_LOOKBACK_SECONDS)
                    before_image = _extract_frame_at(video_path, before_frame_number)
                    after_image = _extract_frame_at(video_path, after_frame_number)
                    if before_image is not None:
                        _save_jpeg(event_dir / "before.jpg", before_image)
                    if after_image is not None:
                        _save_jpeg(event_dir / "after.jpg", after_image)

                    heatmaps = _render_all_heatmaps(crowd_metrics, width, height, frame.image, frame.timestamp_seconds)
                    for heatmap_type, image in heatmaps.items():
                        _save_jpeg(event_dir / f"heatmap_{heatmap_type.lower()}.jpg", image)

                    montage_panels = [
                        ("BEFORE", before_image), ("TRIGGER", frame.image), ("AFTER", after_image), ("ROI", roi_crop if roi_crop.size > 0 else None),
                    ] + [(t, heatmaps.get(t)) for t in ("DENSITY", "PRESSURE", "FLOW_CONGESTION", "RISK", "PREDICTIVE")]
                    montage = _build_montage(
                        montage_panels,
                        f"Event {event_index:04d}  t={frame.timestamp_seconds:.2f}s  ACUTE_HAZARD (deterministic-only scan)",
                    )
                    _save_jpeg(event_dir / "montage.jpg", montage)

                    artifact = EventArtifact(
                        event_index=event_index, frame_number=frame.frame_number,
                        timestamp_seconds=frame.timestamp_seconds, trigger_reason=trigger_decision.reason,
                        corroborating_signals=list(acute_signal.corroborating_signals),
                        z_scores=dict(acute_signal.z_scores), raw_values=dict(acute_signal.raw_values),
                        spatial_active_cell_fraction=active_frac, spatial_largest_component_fraction=largest_frac,
                        roi_bbox=tuple(roi_bbox), heatmap_types_available=list(heatmaps.keys()),
                        before_frame_available=before_image is not None, after_frame_available=after_image is not None,
                    )
                    _write_event_report(event_dir / "report.md", artifact, mode="deterministic-only")
                    events.append(artifact)

            prev_frame = frame

        return events


# ============================================================
# Full-chain mode (real production entrypoint, zero mocking)
# ============================================================

def _run_full_chain(video_path: Path, events_dir: Path) -> list[EventArtifact]:
    db = SessionLocal()
    try:
        user = db.query(User).first()
        if user is None:
            raise RuntimeError("No User exists in the database — cannot create a real AnalysisSession.")

        with MP4FrameSource(video_path, frame_step=1) as source:
            metadata = source.get_metadata()

        storage_filename = f"{uuid.uuid4()}.mp4"
        storage_dir = REPO_ROOT / settings.VIDEO_STORAGE_PATH
        storage_dir.mkdir(parents=True, exist_ok=True)
        storage_path = storage_dir / storage_filename
        shutil.copyfile(video_path, storage_path)

        video_asset = VideoAsset(
            original_filename=f"VALIDATION_HARNESS_{video_path.name}",
            storage_filename=storage_filename,
            file_size_bytes=storage_path.stat().st_size,
            mime_type="video/mp4",
            uploaded_by=user.id,
            fps=metadata.fps, width=metadata.width, height=metadata.height,
            duration_seconds=metadata.duration_seconds, frame_count=metadata.frame_count,
        )
        db.add(video_asset)
        db.commit()
        db.refresh(video_asset)

        session = session_service.create_session(db, video_asset.id, user.id)
        session = session_service.start_session(db, session)

        orchestrator = AnalysisOrchestrator(session.id)
        orchestrator.run()

        db.expire_all()
        packages = (
            db.query(EvidencePackage)
            .filter(EvidencePackage.session_id == session.id, EvidencePackage.trigger_type == TriggerType.ACUTE_HAZARD)
            .order_by(EvidencePackage.timestamp_seconds.asc())
            .all()
        )

        events: list[EventArtifact] = []
        for pkg in packages:
            event_index = len(events) + 1
            event_dir = events_dir / f"event_{event_index:04d}"
            event_dir.mkdir(parents=True, exist_ok=True)

            frame_storage_dir = REPO_ROOT / Path(settings.EVIDENCE_FRAMES_STORAGE_PATH)
            trigger_src = frame_storage_dir / pkg.representative_frame_path
            roi_src = frame_storage_dir / pkg.roi_crop_path
            if trigger_src.exists():
                shutil.copyfile(trigger_src, event_dir / "trigger.jpg")
            if roi_src.exists():
                shutil.copyfile(roi_src, event_dir / "roi.jpg")

            before_frame_number = max(0, pkg.frame_number - round(metadata.fps * _CONTEXT_LOOKBACK_SECONDS))
            after_frame_number = pkg.frame_number + round(metadata.fps * _CONTEXT_LOOKBACK_SECONDS)
            before_image = _extract_frame_at(storage_path, before_frame_number)
            after_image = _extract_frame_at(storage_path, after_frame_number)
            if before_image is not None:
                _save_jpeg(event_dir / "before.jpg", before_image)
            if after_image is not None:
                _save_jpeg(event_dir / "after.jpg", after_image)

            heatmap_paths: dict[str, Path] = {}
            for heatmap_type in _HEATMAP_ORDER:
                nearest = (
                    db.query(HeatmapSnapshot)
                    .filter(HeatmapSnapshot.session_id == session.id, HeatmapSnapshot.heatmap_type == heatmap_type)
                    .order_by(func_abs_diff(HeatmapSnapshot.timestamp_seconds, pkg.timestamp_seconds))
                    .first()
                )
                if nearest is not None:
                    src = REPO_ROOT / Path(settings.HEATMAP_STORAGE_PATH) / nearest.file_path
                    if src.exists():
                        dest = event_dir / f"heatmap_{heatmap_type.value.lower()}.jpg"
                        shutil.copyfile(src, dest)
                        heatmap_paths[heatmap_type.value] = dest

            trigger_image = cv2.imread(str(event_dir / "trigger.jpg")) if (event_dir / "trigger.jpg").exists() else None
            roi_image = cv2.imread(str(event_dir / "roi.jpg")) if (event_dir / "roi.jpg").exists() else None
            montage_panels = [
                ("BEFORE", before_image), ("TRIGGER", trigger_image), ("AFTER", after_image), ("ROI", roi_image),
            ] + [(t, cv2.imread(str(p))) for t, p in heatmap_paths.items()]
            montage = _build_montage(
                montage_panels,
                f"Event {event_index:04d}  t={pkg.timestamp_seconds:.2f}s  ACUTE_HAZARD (full-chain, real inference)",
            )
            _save_jpeg(event_dir / "montage.jpg", montage)

            (event_dir / "evidence_package.json").write_text(
                json.dumps({
                    "package_id": str(pkg.id), "frame_number": pkg.frame_number,
                    "timestamp_seconds": pkg.timestamp_seconds, "trigger_reason": pkg.trigger_reason,
                    "crowd_metrics_summary": pkg.crowd_metrics_summary, "vision_observations_present": pkg.vision_observations_present,
                    "confidence": pkg.confidence, "binding_constraint": pkg.binding_constraint,
                    "complete": pkg.complete, "missing_evidence": pkg.missing_evidence,
                    "contradictions": pkg.contradictions, "acute_hazard_signal_snapshot": pkg.acute_hazard_signal_snapshot,
                    "event_window": pkg.event_window,
                }, indent=2, default=str),
                encoding="utf-8",
            )

            decision = db.query(DecisionResultRow).filter(DecisionResultRow.evidence_package_id == pkg.id).first()
            incident_exists = False
            if decision is not None:
                (event_dir / "decision_result.json").write_text(
                    json.dumps({
                        "decision_id": str(decision.id), "outcome": decision.outcome.value,
                        "reasoning_summary": decision.reasoning_summary, "recommendation": decision.recommendation.value if decision.recommendation else None,
                        "event_classification": decision.event_classification.value if decision.event_classification else None,
                        "structured_report": decision.structured_report, "abstention_reason": decision.abstention_reason,
                        "confidence": decision.confidence,
                    }, indent=2, default=str),
                    encoding="utf-8",
                )
                incident_link = db.query(IncidentEvidence).filter(IncidentEvidence.decision_result_id == decision.id).first()
                if incident_link is not None:
                    incident_row = db.get(Incident, incident_link.incident_id)
                    incident_exists = incident_row is not None
                    if incident_row is not None:
                        (event_dir / "incident.json").write_text(
                            json.dumps({
                                "incident_id": str(incident_row.id), "lifecycle_status": incident_row.lifecycle_status.value,
                                "priority": incident_row.priority.value, "created_at": str(incident_row.created_at),
                            }, indent=2),
                            encoding="utf-8",
                        )

            diagnosis_stage, diagnosis_explanation = _diagnose_stage(pkg, decision, incident_exists)

            items = db.query(EvidenceItem).filter(EvidenceItem.evidence_package_id == pkg.id).all()
            observation_categories = [item.category.value for item in items]
            contradiction_types = [c.get("contradiction_type") for c in (pkg.contradictions or [])]

            artifact = EventArtifact(
                event_index=event_index, frame_number=pkg.frame_number, timestamp_seconds=pkg.timestamp_seconds,
                trigger_reason=pkg.trigger_reason,
                corroborating_signals=(pkg.acute_hazard_signal_snapshot or {}).get("corroborating_signals", []),
                z_scores=(pkg.acute_hazard_signal_snapshot or {}).get("z_scores", {}), raw_values={},
                spatial_active_cell_fraction=None, spatial_largest_component_fraction=None,
                roi_bbox=tuple(pkg.roi_bbox), heatmap_types_available=list(heatmap_paths.keys()),
                before_frame_available=before_image is not None, after_frame_available=after_image is not None,
                vlm_call_succeeded=pkg.vision_observations_present, vision_observation_categories=observation_categories,
                evidence_complete=pkg.complete, evidence_missing=pkg.missing_evidence, contradiction_types=contradiction_types,
                decision_outcome=decision.outcome.value if decision else None,
                abstention_reason=decision.abstention_reason if decision else None,
                event_classification=decision.event_classification.value if (decision and decision.event_classification) else None,
                incident_created=incident_exists if decision is not None else None,
                diagnosis_stage=diagnosis_stage, diagnosis_explanation=diagnosis_explanation,
            )
            _write_event_report(event_dir / "report.md", artifact, mode="full-chain")
            events.append(artifact)

        return events
    finally:
        db.close()


def func_abs_diff(column, value: float):
    """SQLAlchemy ORDER BY helper: sorts by |column - value| ascending so the
    FIRST row from .first() is the nearest match — no raw SQL string needed."""
    from sqlalchemy import func
    return func.abs(column - value)


# ============================================================
# Single-video orchestration (used by both single-video CLI usage and each
# manifest entry in batch mode)
# ============================================================

def validate_one_video(
    video_path: Path, output_dir: Path, full_chain: bool, sample_interval: int,
    start_time: Optional[float], end_time: Optional[float], max_duration: Optional[float],
    save_crops: bool, generate_report: bool, sample_id: Optional[str] = None,
) -> dict:
    if not video_path.exists():
        raise FileNotFoundError(f"Video not found: {video_path}")

    video_sha256 = compute_sha256(video_path)
    run_id = make_run_id()
    run_dir = output_dir / run_id
    events_dir = run_dir / "events"
    events_dir.mkdir(parents=True, exist_ok=True)

    with MP4FrameSource(video_path, frame_step=1) as source:
        metadata = source.get_metadata()

    tmp_trimmed = None
    effective_path = video_path
    if start_time is not None or end_time is not None or max_duration is not None:
        tmp_trimmed = run_dir / f"trimmed_{video_path.stem}.mp4"
        effective_path = _trim_video(video_path, tmp_trimmed, start_time, end_time, max_duration)

    mode = "full-chain" if full_chain else "deterministic-only"
    run_metadata = {
        "run_id": run_id,
        "sample_id": sample_id,
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "source_filename": video_path.name,
        "source_path": str(video_path),
        "source_sha256": video_sha256,
        "video_duration_seconds": metadata.duration_seconds,
        "video_fps": metadata.fps,
        "video_width": metadata.width,
        "video_height": metadata.height,
        "video_frame_count": metadata.frame_count,
        "selected_window": {"start_time": start_time, "end_time": end_time, "max_duration": max_duration},
        "sample_interval": sample_interval,
        "save_crops": save_crops,
        "run_mode": mode,
        "active_models": {
            "detector": "YOLO11nDetector", "tracker": "ByteTrackAdapter", "optical_flow": "DISOpticalFlowAdapter",
            "vision_model": "MiniCPMVisionModel (only if this run is --full-chain)",
            "reasoner": "Reasoner/Qwen3-8B (only if this run is --full-chain)",
            "verifier": "Verifier/Qwen3-8B think=True (only if outcome=INCIDENT in --full-chain)",
        },
        "acute_hazard_config": {
            "ACUTE_HAZARD_ZSCORE_THRESHOLD": settings.ACUTE_HAZARD_ZSCORE_THRESHOLD,
            "ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS": settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS,
            "ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS": settings.ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS,
            "ACUTE_HAZARD_BASELINE_EMA_ALPHA": settings.ACUTE_HAZARD_BASELINE_EMA_ALPHA,
            "ACUTE_HAZARD_COOLDOWN_SECONDS": settings.ACUTE_HAZARD_COOLDOWN_SECONDS,
            "signal_order": list(SIGNAL_ORDER),
        },
    }
    (run_dir / "run_metadata.json").write_text(json.dumps(run_metadata, indent=2), encoding="utf-8")

    if full_chain:
        events = _run_full_chain(effective_path, events_dir)
    else:
        events = _run_deterministic_scan(effective_path, events_dir, sample_interval, start_time=None, end_time=None)

    if not save_crops:
        for event_dir in events_dir.glob("event_*"):
            for name in ("before.jpg", "after.jpg", "trigger.jpg", "roi.jpg"):
                candidate = event_dir / name
                if candidate.exists():
                    candidate.unlink()

    if tmp_trimmed is not None and tmp_trimmed.exists():
        tmp_trimmed.unlink()

    incident_count = sum(1 for e in events if e.incident_created)
    watch_count = sum(1 for e in events if e.decision_outcome == "WATCH")
    abstain_count = sum(1 for e in events if e.decision_outcome == "ABSTAIN")
    no_incident_count = sum(1 for e in events if e.decision_outcome == "NO_INCIDENT")

    results = {
        "run_metadata": run_metadata,
        "acute_hazard_trigger_count": len(events),
        "semantic_analyses_run": sum(1 for e in events if e.vlm_call_succeeded is not None) if full_chain else 0,
        "outcome_counts": {"INCIDENT": incident_count, "WATCH": watch_count, "ABSTAIN": abstain_count, "NO_INCIDENT": no_incident_count},
        "events": [vars(e) for e in events],
    }
    (run_dir / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")

    if generate_report:
        summary_lines = [
            f"# Validation run {run_id}",
            "",
            f"- source: `{video_path}`",
            f"- sha256: `{video_sha256}`",
            f"- duration: {metadata.duration_seconds:.2f}s @ {metadata.fps:.1f}fps, {metadata.width}x{metadata.height}",
            f"- mode: **{mode}**",
            f"- ACUTE_HAZARD triggers: {len(events)}",
        ]
        if full_chain:
            summary_lines += [
                f"- outcome counts: INCIDENT={incident_count}, WATCH={watch_count}, ABSTAIN={abstain_count}, NO_INCIDENT={no_incident_count}",
            ]
        summary_lines += ["", "## Events"]
        for e in events:
            summary_lines.append(f"- [Event {e.event_index:04d}](events/event_{e.event_index:04d}/report.md) — t={e.timestamp_seconds:.2f}s, outcome={e.decision_outcome or 'N/A (deterministic-only)'}")
        (run_dir / "summary.md").write_text("\n".join(summary_lines), encoding="utf-8")

    return {"run_id": run_id, "run_dir": str(run_dir), "results": results}


# ============================================================
# Batch mode (Step 16)
# ============================================================

def _run_batch(manifest_path: Path, output_dir: Path, full_chain: bool, sample_interval: int, save_crops: bool, generate_report: bool) -> None:
    entries = json.loads(manifest_path.read_text(encoding="utf-8"))
    batch_run_id = make_run_id(prefix="batch_")
    batch_dir = output_dir / batch_run_id
    batch_dir.mkdir(parents=True, exist_ok=True)

    seen_sha256: dict[str, str] = {}
    duplicate_pairs: list[tuple[str, str]] = []
    per_sample_results = []

    for entry in entries:
        sample_id = entry["sample_id"]
        video_path = Path(entry["path"])
        ground_truth_status = entry.get("ground_truth_status") or _NO_GROUND_TRUTH

        if not video_path.exists():
            per_sample_results.append({"sample_id": sample_id, "status": "MISSING_FILE", "path": str(video_path)})
            continue

        sha256 = compute_sha256(video_path)
        if sha256 in seen_sha256:
            duplicate_pairs.append((seen_sha256[sha256], sample_id))
            per_sample_results.append({
                "sample_id": sample_id, "status": "DUPLICATE_OF", "duplicate_of": seen_sha256[sha256], "sha256": sha256,
            })
            continue
        seen_sha256[sha256] = sample_id

        sample_output_dir = batch_dir / "samples"
        outcome = validate_one_video(
            video_path, sample_output_dir, full_chain, sample_interval,
            entry.get("event_start"), entry.get("event_end"), None, save_crops, generate_report, sample_id=sample_id,
        )
        per_sample_results.append({
            "sample_id": sample_id, "status": "PROCESSED", "sha256": sha256,
            "ground_truth_status": ground_truth_status, "manifest_entry": entry,
            **outcome["results"],
        })

    total_triggers = sum(r.get("acute_hazard_trigger_count", 0) for r in per_sample_results if r["status"] == "PROCESSED")
    total_incidents = sum(r.get("outcome_counts", {}).get("INCIDENT", 0) for r in per_sample_results if r["status"] == "PROCESSED")
    total_abstains = sum(r.get("outcome_counts", {}).get("ABSTAIN", 0) for r in per_sample_results if r["status"] == "PROCESSED")
    total_watch = sum(r.get("outcome_counts", {}).get("WATCH", 0) for r in per_sample_results if r["status"] == "PROCESSED")

    has_any_ground_truth = any(
        r.get("ground_truth_status") not in (None, _NO_GROUND_TRUTH) for r in per_sample_results if r["status"] == "PROCESSED"
    )

    aggregate = {
        "batch_run_id": batch_run_id,
        "manifest_path": str(manifest_path),
        "run_mode": "full-chain" if full_chain else "deterministic-only",
        "sample_count": len(entries),
        "processed_count": sum(1 for r in per_sample_results if r["status"] == "PROCESSED"),
        "missing_file_count": sum(1 for r in per_sample_results if r["status"] == "MISSING_FILE"),
        "duplicate_count": sum(1 for r in per_sample_results if r["status"] == "DUPLICATE_OF"),
        "duplicate_pairs": duplicate_pairs,
        "total_acute_hazard_triggers": total_triggers,
        "total_semantic_analyses": sum(r.get("semantic_analyses_run", 0) for r in per_sample_results if r["status"] == "PROCESSED"),
        "total_outcome_counts": {"INCIDENT": total_incidents, "WATCH": total_watch, "ABSTAIN": total_abstains},
        "accuracy_metrics": "NOT COMPUTED — " + _NO_GROUND_TRUTH if not has_any_ground_truth else "PARTIAL — some samples have ground_truth_status; per-sample only, no aggregate precision/recall computed this phase",
        "per_sample": per_sample_results,
    }
    (batch_dir / "aggregate.json").write_text(json.dumps(aggregate, indent=2, default=str), encoding="utf-8")

    lines = [
        f"# Batch validation {batch_run_id}",
        "",
        f"- manifest: `{manifest_path}`",
        f"- samples: {len(entries)} ({aggregate['processed_count']} processed, {aggregate['missing_file_count']} missing, {aggregate['duplicate_count']} duplicate)",
        f"- total ACUTE_HAZARD triggers: {total_triggers}",
        f"- total outcomes: INCIDENT={total_incidents}, WATCH={total_watch}, ABSTAIN={total_abstains}",
        f"- accuracy: {aggregate['accuracy_metrics']}",
        "",
        "## Per-sample",
    ]
    for r in per_sample_results:
        lines.append(f"- {r['sample_id']}: {r['status']}" + (f" (triggers={r.get('acute_hazard_trigger_count')})" if r["status"] == "PROCESSED" else ""))
    (batch_dir / "aggregate.md").write_text("\n".join(lines), encoding="utf-8")

    print(f"Batch complete. Aggregate written to {batch_dir}")
    print(json.dumps({k: v for k, v in aggregate.items() if k != "per_sample"}, indent=2, default=str))


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Acute-Hazard Validation Harness")
    parser.add_argument("video", nargs="?", help="Path to a single video file to validate")
    parser.add_argument("--manifest", type=str, default=None, help="Path to a JSON manifest for batch validation")
    parser.add_argument("--output-dir", type=str, default=str(REPO_ROOT / "validation_runs"))
    parser.add_argument("--sample-interval", type=int, default=1, help="Deterministic-scan-only: process every Nth frame (default 1 = every frame)")
    parser.add_argument("--start-time", type=float, default=None, help="Seconds into the video to begin analysis")
    parser.add_argument("--end-time", type=float, default=None, help="Seconds into the video to stop analysis")
    parser.add_argument("--max-duration", type=float, default=None, help="Cap total analyzed duration in seconds")
    parser.add_argument("--save-crops", dest="save_crops", action="store_true", default=True)
    parser.add_argument("--no-save-crops", dest="save_crops", action="store_false")
    parser.add_argument("--full-chain", action="store_true", default=False, help="Run the REAL VLM->Evidence->Abstention->Reasoner->Verifier->Incident chain (no mocking). Default is a fast deterministic-only scan.")
    parser.add_argument("--report", dest="generate_report", action="store_true", default=True)
    parser.add_argument("--no-report", dest="generate_report", action="store_false")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    if args.manifest:
        _run_batch(Path(args.manifest), output_dir, args.full_chain, args.sample_interval, args.save_crops, args.generate_report)
        return

    if not args.video:
        print("Usage: python scripts/validate_acute_event_video.py <video_path> [options]")
        print("   or: python scripts/validate_acute_event_video.py --manifest <manifest.json> [options]")
        sys.exit(1)

    outcome = validate_one_video(
        Path(args.video), output_dir, args.full_chain, args.sample_interval,
        args.start_time, args.end_time, args.max_duration, args.save_crops, args.generate_report,
    )
    print(f"Run complete: {outcome['run_dir']}")
    print(json.dumps({k: v for k, v in outcome["results"].items() if k != "events"}, indent=2, default=str))
    for e in outcome["results"]["events"]:
        print(f"  event_{e['event_index']:04d}: t={e['timestamp_seconds']:.2f}s outcome={e.get('decision_outcome')}")


if __name__ == "__main__":
    main()
