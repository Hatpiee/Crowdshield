"""Sprint-0 CPU Load Test phase, Step 20: unit tests for the benchmark
harness's own bookkeeping logic. NO real Ollama calls anywhere in this
file — only pure-function/dataclass/serialization behavior, per the
governing task's own "do NOT add dozens of real Ollama calls to unit
tests" instruction (this benchmark's real-inference execution is a
separate, explicit, manually-run phase, not part of the automated suite).
"""

import json
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / "scripts"))

from benchmark_sprint0_cpu import (  # noqa: E402
    FailureCounter,
    ResourceSampler,
    collect_machine_metadata,
    compute_degradation_percent,
    latency_summary,
    percentile,
    video_metadata,
)

from tests.fixtures.synthetic_video import generate_synthetic_mp4  # noqa: E402


def test_percentile_matches_known_values():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert percentile(values, 50) == 3.0
    assert percentile(values, 0) == 1.0
    assert percentile(values, 100) == 5.0


def test_percentile_empty_list_returns_none():
    assert percentile([], 50) is None


def test_latency_summary_empty_is_all_none_not_zero():
    summary = latency_summary([])
    assert summary["count"] == 0
    assert summary["mean"] is None
    assert summary["p95"] is None
    # Critical: an empty/incomplete run must never silently report 0.0
    # latency (Step 14/20's "incomplete-run handling" requirement) — every
    # field is None (unknown), never a fabricated zero.


def test_latency_summary_real_values():
    values = [1.0, 2.0, 3.0, 4.0, 10.0]
    summary = latency_summary(values)
    assert summary["count"] == 5
    assert summary["max"] == 10.0
    assert summary["min"] == 1.0
    assert summary["mean"] == pytest.approx(4.0)


def test_compute_degradation_percent_throughput_drop():
    # baseline 30fps -> contended 15fps = 50% degradation
    assert compute_degradation_percent(30.0, 15.0) == pytest.approx(50.0)


def test_compute_degradation_percent_no_change_is_zero():
    assert compute_degradation_percent(30.0, 30.0) == pytest.approx(0.0)


def test_compute_degradation_percent_missing_inputs_returns_none_not_fabricated():
    assert compute_degradation_percent(None, 15.0) is None
    assert compute_degradation_percent(30.0, None) is None
    assert compute_degradation_percent(0.0, 15.0) is None


def test_failure_counter_records_success_and_failure_separately():
    counter = FailureCounter()
    counter.record_success()
    counter.record_success()
    counter.record_failure("VLM", TimeoutError("timed out"), elapsed_seconds=60.0, timed_out=True, retry_count=1)
    counter.record_dropped()

    assert counter.attempted == 3
    assert counter.successful == 2
    assert counter.failed == 1
    assert counter.timed_out == 1
    assert counter.retried == 1
    assert counter.dropped == 1
    assert len(counter.records) == 1
    assert counter.records[0].stage == "VLM"
    assert counter.records[0].exception_type == "TimeoutError"
    assert counter.records[0].elapsed_seconds == 60.0


def test_failure_counter_never_collapses_timeout_into_zero_latency():
    counter = FailureCounter()
    counter.record_failure("REASONER", RuntimeError("x"), elapsed_seconds=171.4)
    assert counter.records[0].elapsed_seconds == 171.4
    assert counter.records[0].elapsed_seconds != 0.0


def test_failure_counter_serializes_to_json():
    counter = FailureCounter()
    counter.record_success()
    counter.record_failure("VLM", ValueError("bad"), elapsed_seconds=1.5)
    from dataclasses import asdict

    payload = json.dumps(asdict(counter), default=str)
    parsed = json.loads(payload)
    assert parsed["attempted"] == 2
    assert parsed["records"][0]["exception_type"] == "ValueError"


def test_collect_machine_metadata_never_fabricates_missing_fields():
    metadata = collect_machine_metadata()
    # Real, always-obtainable fields must be present and non-None.
    assert metadata["os"]
    assert metadata["python_version"]
    assert metadata["logical_cores"] and metadata["logical_cores"] > 0
    assert metadata["total_ram_gb"] > 0
    # vlm_model/llm_model/max_concurrent_semantic_analyses come straight
    # from settings — real config values, not guessed.
    assert metadata["vlm_model"]
    assert metadata["llm_model"]
    assert isinstance(metadata["max_concurrent_semantic_analyses"], int)


def test_video_metadata_real_sha256_and_probe(tmp_path):
    video_path = tmp_path / "tiny.mp4"
    generate_synthetic_mp4(video_path, num_frames=10, width=64, height=64, fps=10.0)

    metadata = video_metadata(video_path)

    assert len(metadata["sha256"]) == 64  # real hex-encoded SHA256
    assert metadata["frame_count"] == 10
    assert metadata["width"] == 64
    assert metadata["height"] == 64
    assert metadata["fps"] == pytest.approx(10.0)

    # Determinism: re-hashing the same file must produce the same digest.
    assert video_metadata(video_path)["sha256"] == metadata["sha256"]


def test_video_metadata_different_files_have_different_hashes(tmp_path):
    video_a = tmp_path / "a.mp4"
    video_b = tmp_path / "b.mp4"
    generate_synthetic_mp4(video_a, num_frames=5)
    generate_synthetic_mp4(video_b, num_frames=8)

    assert video_metadata(video_a)["sha256"] != video_metadata(video_b)["sha256"]


def test_resource_sampler_stop_before_start_data_returns_none_not_zero():
    sampler = ResourceSampler()
    result = sampler.stop()
    # No samples were ever collected — every mean/peak must be None, never
    # a fabricated 0.0 that would misleadingly read as "measured zero load."
    assert result["backend_cpu_percent_mean"] is None
    assert result["backend_rss_mb_peak"] is None
    assert result["sample_count"] == 0


def test_resource_sampler_real_short_run_collects_real_samples():
    sampler = ResourceSampler(interval_seconds=0.05)
    sampler.start()
    import time

    time.sleep(0.3)
    result = sampler.stop()

    assert result["sample_count"] > 0
    assert result["backend_rss_mb_mean"] is not None
    assert result["backend_rss_mb_mean"] > 0


# ============================================================
# Report generation (Step 17/20) — incomplete-run handling
# ============================================================

def test_report_generation_marks_missing_scenarios_as_not_run(tmp_path):
    from generate_sprint0_report import generate_report

    report = generate_report(tmp_path)  # empty results dir — nothing was run

    assert "NOT RUN" in report
    assert "# Loop-A Baseline" in report
    assert "# Full Semantic Chain" in report
    # Must never fabricate a scenario's numbers when its JSON is absent.
    assert "None" not in report.split("# Hardware")[0]  # Executive Summary stays honestly blank, not "None fps"


def test_report_generation_uses_real_baseline_json(tmp_path):
    from generate_sprint0_report import generate_report

    baseline = {
        "scenario": "BASELINE_LOOP_A_ONLY",
        "video": {"path": "x.mp4", "duration_seconds": 20.1, "frame_count": 603, "sha256": "a" * 64},
        "environment": {"os": "test-os", "vlm_model": "test-vlm"},
        "loop_a": {
            "loop_a_wall_seconds": 20.0, "loop_a_effective_fps": 30.15,
            "frame_latency": {"mean": 0.03, "p95": 0.05, "p99": 0.06, "max": 0.08, "min": 0.01, "count": 602},
        },
        "resources": {"backend_cpu_percent_mean": 45.0, "backend_cpu_percent_max": 80.0, "backend_rss_mb_mean": 500.0, "backend_rss_mb_peak": 600.0},
    }
    (tmp_path / "baseline_loop_a_only.json").write_text(json.dumps(baseline), encoding="utf-8")

    report = generate_report(tmp_path)

    assert "30.15" in report
    assert "test-os" in report
    assert "# Loop-A + VLM\nNOT RUN." in report or "NOT RUN." in report.split("# Loop-A + VLM")[1]
