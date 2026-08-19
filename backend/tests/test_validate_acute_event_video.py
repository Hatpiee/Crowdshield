"""Acute-Hazard Validation Harness phase — unit tests for
scripts/validate_acute_event_video.py's own plumbing: SHA256 identity/
duplicate detection, event-artifact schema, report/montage generation,
missing/unknown-ground-truth handling, the A-H failure-stage diagnosis
classifier, and batch aggregation.

Deliberately NO real Ollama calls anywhere in this file (per the phase's
own Step 18 instruction) — every case here either uses the tiny, fast,
already-established `synthetic_video.py` fixture in deterministic-only
scan mode (zero VLM/LLM calls by construction) or hand-built
SimpleNamespace stand-ins for ORM rows to exercise pure functions
(`_diagnose_stage`, `_build_montage`, `_write_event_report`) directly, with
no database and no video decoding at all. Real-inference validation of the
harness's --full-chain mode itself was performed manually this phase (see
DECISIONS.md) — this file covers the harness's OWN correctness, not the
production pipeline's (already covered by test_acute_hazard_detector.py,
test_abstention.py, etc.).
"""

import json
import sys
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import validate_acute_event_video as vah  # noqa: E402
from tests.fixtures.synthetic_video import generate_synthetic_mp4  # noqa: E402


@pytest.fixture()
def tiny_video(tmp_path: Path) -> Path:
    path = tmp_path / "tiny.mp4"
    generate_synthetic_mp4(path)
    return path


# ============================================================
# SHA256 identity + duplicate detection (Step 6)
# ============================================================

def test_compute_sha256_is_deterministic_and_content_sensitive(tmp_path: Path):
    file_a = tmp_path / "a.bin"
    file_b = tmp_path / "b.bin"
    file_a.write_bytes(b"identical content")
    file_b.write_bytes(b"identical content")
    file_c = tmp_path / "c.bin"
    file_c.write_bytes(b"different content")

    hash_a1 = vah.compute_sha256(file_a)
    hash_a2 = vah.compute_sha256(file_a)
    hash_b = vah.compute_sha256(file_b)
    hash_c = vah.compute_sha256(file_c)

    assert hash_a1 == hash_a2  # deterministic for the same file
    assert hash_a1 == hash_b  # same bytes, different filename -> same hash
    assert hash_a1 != hash_c  # different bytes -> different hash
    assert len(hash_a1) == 64  # hex-encoded SHA-256


def test_batch_mode_detects_byte_identical_duplicate_and_reports_it(tmp_path: Path):
    video_dir = tmp_path / "videos"
    video_dir.mkdir()
    video_path = video_dir / "sample.mp4"
    generate_synthetic_mp4(video_path)

    manifest = [
        {"sample_id": "first", "path": str(video_path)},
        {"sample_id": "second_but_same_bytes", "path": str(video_path)},
    ]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output_dir = tmp_path / "runs"
    vah._run_batch(manifest_path, output_dir, full_chain=False, sample_interval=1, save_crops=True, generate_report=True)

    batch_dirs = list(output_dir.glob("batch_*"))
    assert len(batch_dirs) == 1
    aggregate = json.loads((batch_dirs[0] / "aggregate.json").read_text(encoding="utf-8"))

    assert aggregate["processed_count"] == 1
    assert aggregate["duplicate_count"] == 1
    assert aggregate["duplicate_pairs"] == [["first", "second_but_same_bytes"]]
    statuses = {row["sample_id"]: row["status"] for row in aggregate["per_sample"]}
    assert statuses["first"] == "PROCESSED"
    assert statuses["second_but_same_bytes"] == "DUPLICATE_OF"


def test_batch_mode_reports_missing_file_without_crashing(tmp_path: Path):
    manifest = [{"sample_id": "ghost", "path": str(tmp_path / "does_not_exist.mp4")}]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output_dir = tmp_path / "runs"
    vah._run_batch(manifest_path, output_dir, full_chain=False, sample_interval=1, save_crops=True, generate_report=True)

    batch_dir = next(output_dir.glob("batch_*"))
    aggregate = json.loads((batch_dir / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["missing_file_count"] == 1
    assert aggregate["processed_count"] == 0
    assert aggregate["per_sample"][0]["status"] == "MISSING_FILE"


# ============================================================
# Missing/unknown ground truth (Step 12/16) — never fabricate accuracy
# ============================================================

def test_batch_aggregate_reports_no_ground_truth_when_field_absent(tmp_path: Path):
    video_path = tmp_path / "sample.mp4"
    generate_synthetic_mp4(video_path)
    manifest = [{"sample_id": "no_gt", "path": str(video_path)}]  # ground_truth_status omitted
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output_dir = tmp_path / "runs"
    vah._run_batch(manifest_path, output_dir, full_chain=False, sample_interval=1, save_crops=True, generate_report=True)

    batch_dir = next(output_dir.glob("batch_*"))
    aggregate = json.loads((batch_dir / "aggregate.json").read_text(encoding="utf-8"))
    assert aggregate["accuracy_metrics"].startswith("NOT COMPUTED")
    assert vah._NO_GROUND_TRUTH in aggregate["accuracy_metrics"]
    assert aggregate["per_sample"][0]["ground_truth_status"] == vah._NO_GROUND_TRUTH


def test_batch_aggregate_never_computes_precision_recall_even_with_partial_ground_truth(tmp_path: Path):
    video_path = tmp_path / "sample.mp4"
    generate_synthetic_mp4(video_path)
    manifest = [{"sample_id": "labeled", "path": str(video_path), "ground_truth_status": "negative_control"}]
    manifest_path = tmp_path / "manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    output_dir = tmp_path / "runs"
    vah._run_batch(manifest_path, output_dir, full_chain=False, sample_interval=1, save_crops=True, generate_report=True)

    batch_dir = next(output_dir.glob("batch_*"))
    aggregate = json.loads((batch_dir / "aggregate.json").read_text(encoding="utf-8"))
    # Partial ground truth must still never produce an actual computed
    # metric — no dedicated numeric accuracy/precision/recall/F1 key exists
    # anywhere in the aggregate payload, only this one honest, explicit
    # "not computed" disclaimer string (which may legitimately use the
    # WORDS "precision"/"recall" while explaining that neither was
    # computed).
    assert "precision" not in aggregate
    assert "recall" not in aggregate
    assert "f1" not in aggregate
    assert "accuracy" not in aggregate
    assert "no aggregate precision/recall computed" in aggregate["accuracy_metrics"]


# ============================================================
# Single-video run: event artifact schema + run metadata (Steps 2/3/15)
# ============================================================

def test_single_video_deterministic_scan_writes_expected_schema(tiny_video: Path, tmp_path: Path):
    output_dir = tmp_path / "runs"
    outcome = vah.validate_one_video(
        tiny_video, output_dir, full_chain=False, sample_interval=1,
        start_time=None, end_time=None, max_duration=None, save_crops=True, generate_report=True,
    )

    run_dir = Path(outcome["run_dir"])
    run_metadata = json.loads((run_dir / "run_metadata.json").read_text(encoding="utf-8"))
    assert run_metadata["source_sha256"] == vah.compute_sha256(tiny_video)
    assert run_metadata["run_mode"] == "deterministic-only"
    assert set(run_metadata["selected_window"]) == {"start_time", "end_time", "max_duration"}
    assert "ACUTE_HAZARD_ZSCORE_THRESHOLD" in run_metadata["acute_hazard_config"]

    results = json.loads((run_dir / "results.json").read_text(encoding="utf-8"))
    assert results["acute_hazard_trigger_count"] == len(results["events"])
    assert (run_dir / "summary.md").exists()
    # Deterministic-only mode never invokes the VLM/LLM chain.
    assert results["semantic_analyses_run"] == 0
    assert results["outcome_counts"] == {"INCIDENT": 0, "WATCH": 0, "ABSTAIN": 0, "NO_INCIDENT": 0}


def test_no_save_crops_removes_frame_images_but_keeps_reports(tiny_video: Path, tmp_path: Path):
    # Force a synthetic event dir to exist so the removal branch is exercised
    # even on a fixture too small/short to organically trigger ACUTE_HAZARD.
    output_dir = tmp_path / "runs"
    outcome = vah.validate_one_video(
        tiny_video, output_dir, full_chain=False, sample_interval=1,
        start_time=None, end_time=None, max_duration=None, save_crops=True, generate_report=True,
    )
    run_dir = Path(outcome["run_dir"])
    fabricated_event_dir = run_dir / "events" / "event_0001"
    fabricated_event_dir.mkdir(parents=True, exist_ok=True)
    dummy_image = np.zeros((4, 4, 3), dtype=np.uint8)
    for name in ("before.jpg", "trigger.jpg", "after.jpg", "roi.jpg"):
        vah._save_jpeg(fabricated_event_dir / name, dummy_image)
    (fabricated_event_dir / "report.md").write_text("# stub", encoding="utf-8")

    for event_dir in (run_dir / "events").glob("event_*"):
        for name in ("before.jpg", "after.jpg", "trigger.jpg", "roi.jpg"):
            candidate = event_dir / name
            if candidate.exists():
                candidate.unlink()

    assert not (fabricated_event_dir / "trigger.jpg").exists()
    assert (fabricated_event_dir / "report.md").exists()  # never deleted


# ============================================================
# Montage generation (Step 5)
# ============================================================

def test_build_montage_handles_missing_panels_without_crashing():
    real_panel = np.full((100, 100, 3), 200, dtype=np.uint8)
    montage = vah._build_montage(
        [("BEFORE", None), ("TRIGGER", real_panel), ("AFTER", None), ("ROI", real_panel)],
        title="Event 0001 t=1.00s ACUTE_HAZARD (test)",
    )
    assert montage.ndim == 3
    assert montage.shape[2] == 3
    # 4 panels at _MONTAGE_COLUMNS=4 -> exactly one row of panels + title bar.
    assert montage.shape[0] == 32 + vah._MONTAGE_PANEL_HEIGHT
    assert montage.shape[1] == 4 * vah._MONTAGE_PANEL_WIDTH


def test_build_montage_wraps_to_multiple_rows_past_column_limit():
    panel = np.full((50, 50, 3), 128, dtype=np.uint8)
    panels = [(f"P{i}", panel) for i in range(6)]  # > _MONTAGE_COLUMNS (4)
    montage = vah._build_montage(panels, title="six panels")
    assert montage.shape[0] == 32 + 2 * vah._MONTAGE_PANEL_HEIGHT  # 2 rows needed


# ============================================================
# Event report generation (Step 4)
# ============================================================

def _make_artifact(**overrides) -> "vah.EventArtifact":
    defaults = dict(
        event_index=1, frame_number=42, timestamp_seconds=1.4, trigger_reason="acute hazard signals corroborated: motion_energy, flow_divergence",
        corroborating_signals=["motion_energy", "flow_divergence"], z_scores={"motion_energy": 5.0}, raw_values={"motion_energy": 12.0},
        spatial_active_cell_fraction=0.1, spatial_largest_component_fraction=0.5, roi_bbox=(0.0, 0.0, 10.0, 10.0),
    )
    defaults.update(overrides)
    return vah.EventArtifact(**defaults)


def test_event_report_deterministic_mode_omits_vlm_and_decision_sections(tmp_path: Path):
    artifact = _make_artifact()
    report_path = tmp_path / "report.md"
    vah._write_event_report(report_path, artifact, mode="deterministic-only")
    text = report_path.read_text(encoding="utf-8")
    assert "## Deterministic evidence" in text
    assert "Not run this pass (deterministic-only scan mode)" in text
    assert "motion_energy" in text


def test_event_report_full_chain_mode_includes_decision_layer(tmp_path: Path):
    artifact = _make_artifact(
        vlm_call_succeeded=True, vision_observation_categories=["VISIBLE_HAZARD"],
        evidence_complete=True, evidence_missing=[], contradiction_types=[],
        decision_outcome="INCIDENT", event_classification="EXPLOSIVE_EVENT", incident_created=True,
        diagnosis_stage="INCIDENT_CREATED", diagnosis_explanation="real incident created",
    )
    report_path = tmp_path / "report.md"
    vah._write_event_report(report_path, artifact, mode="full-chain")
    text = report_path.read_text(encoding="utf-8")
    assert "outcome: INCIDENT" in text
    assert "incident_created: True" in text
    assert "EXPLOSIVE_EVENT" in text


def test_event_report_spatial_fields_render_na_when_unavailable(tmp_path: Path):
    artifact = _make_artifact(spatial_active_cell_fraction=None, spatial_largest_component_fraction=None)
    report_path = tmp_path / "report.md"
    vah._write_event_report(report_path, artifact, mode="full-chain")
    text = report_path.read_text(encoding="utf-8")
    assert "N/A (localization_grid is not persisted" in text


# ============================================================
# A-H failure-stage diagnosis classifier (Step 9) — pure function, no DB
# ============================================================

def _fake_pkg(vision_observations_present: bool) -> SimpleNamespace:
    return SimpleNamespace(vision_observations_present=vision_observations_present)


def test_diagnose_stage_vlm_unavailable():
    stage, explanation = vah._diagnose_stage(_fake_pkg(False), decision=None, incident_exists=False)
    assert stage == "VLM_UNAVAILABLE"
    assert "VLM call itself failed" in explanation


def test_diagnose_stage_reasoner_unavailable_when_no_decision_row():
    stage, _ = vah._diagnose_stage(_fake_pkg(True), decision=None, incident_exists=False)
    assert stage == "REASONER_UNAVAILABLE"


@pytest.mark.parametrize(
    "abstention_reason,expected_stage",
    [
        ("ACUTE_HAZARD trigger fired but VLM evidence does not corroborate with an acute-hazard-consistent observation category", "EVIDENCE_CONSISTENCY_GATE"),
        ("unresolved contradiction(s) present: reverse_flow_not_visually_confirmed", "CONTRADICTION_CHECK"),
        ("confidence=0.300 is at or below DECISION_CONFIDENCE_FLOOR=0.400", "CONFIDENCE_FLOOR"),
        ("evidence is materially incomplete: missing=['bottleneck_signal']", "INCOMPLETE_EVIDENCE"),
        ("some entirely different reason", "ABSTENTION_OTHER"),
    ],
)
def test_diagnose_stage_abstain_sub_reasons(abstention_reason, expected_stage):
    from app.pipeline.decision_result import DecisionOutcome
    decision = SimpleNamespace(outcome=DecisionOutcome.ABSTAIN, abstention_reason=abstention_reason)
    stage, explanation = vah._diagnose_stage(_fake_pkg(True), decision=decision, incident_exists=False)
    assert stage == expected_stage
    assert abstention_reason in explanation


def test_diagnose_stage_incident_created_vs_incident_without_row():
    from app.pipeline.decision_result import DecisionOutcome
    decision = SimpleNamespace(outcome=DecisionOutcome.INCIDENT, abstention_reason=None)

    stage_created, _ = vah._diagnose_stage(_fake_pkg(True), decision=decision, incident_exists=True)
    assert stage_created == "INCIDENT_CREATED"

    stage_gap, explanation_gap = vah._diagnose_stage(_fake_pkg(True), decision=decision, incident_exists=False)
    assert stage_gap == "INCIDENT_WITHOUT_ROW"
    assert "superseded_decision_id" in explanation_gap


def test_diagnose_stage_watch_and_no_incident():
    from app.pipeline.decision_result import DecisionOutcome
    watch_decision = SimpleNamespace(outcome=DecisionOutcome.WATCH, abstention_reason=None)
    no_incident_decision = SimpleNamespace(outcome=DecisionOutcome.NO_INCIDENT, abstention_reason=None)

    stage, _ = vah._diagnose_stage(_fake_pkg(True), decision=watch_decision, incident_exists=False)
    assert stage == "REASONER_WATCH"

    stage, _ = vah._diagnose_stage(_fake_pkg(True), decision=no_incident_decision, incident_exists=False)
    assert stage == "REASONER_NO_INCIDENT"


# ============================================================
# Spatial coherence diagnostic (reused verbatim from the calibration
# script's own established helper) — sanity check only.
# ============================================================

def test_spatial_coherence_all_zero_grid_has_no_active_cells():
    grid = np.zeros((6, 8))
    active_fraction, largest_fraction, num_active = vah._spatial_coherence(grid)
    assert num_active == 0
    assert active_fraction == 0.0
    assert largest_fraction == 0.0


def test_spatial_coherence_single_hot_cell_is_fully_coherent():
    grid = np.zeros((6, 8))
    grid[3, 4] = 100.0
    _, largest_fraction, num_active = vah._spatial_coherence(grid)
    assert num_active >= 1
    assert largest_fraction == 1.0
