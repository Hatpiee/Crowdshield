"""Sprint-0 CPU Load Test — human-readable report generator (Step 17).
Reads whatever JSON result files `scripts/benchmark_sprint0_cpu.py` has
already written to `benchmark_results/` and renders
`SPRINT0_CPU_LOAD_TEST_REPORT.md`. A scenario whose JSON file does not
exist is rendered as "NOT RUN" — never silently omitted or fabricated.

Usage: python scripts/generate_sprint0_report.py [--results-dir DIR] [--output PATH]
"""

import argparse
import json
import sys
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

from app.core.config import REPO_ROOT  # noqa: E402
from benchmark_sprint0_cpu import compute_degradation_percent  # noqa: E402


def _load(results_dir: Path, name: str) -> Optional[dict]:
    path = results_dir / f"{name}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _fmt(value, suffix: str = "", digits: int = 2) -> str:
    if value is None:
        return "N/A"
    if isinstance(value, float):
        return f"{value:.{digits}f}{suffix}"
    return f"{value}{suffix}"


def _loop_a_table_row(label: str, loop_a: dict) -> str:
    fl = loop_a.get("frame_latency", {})
    return (
        f"| {label} | {_fmt(loop_a.get('loop_a_effective_fps'))} | "
        f"{_fmt(fl.get('mean') and fl['mean'] * 1000, ' ms')} | "
        f"{_fmt(fl.get('p95') and fl['p95'] * 1000, ' ms')} | "
        f"{_fmt(fl.get('p99') and fl['p99'] * 1000, ' ms')} | "
        f"{_fmt(fl.get('max') and fl['max'] * 1000, ' ms')} |"
    )


def generate_report(results_dir: Path) -> str:
    baseline = _load(results_dir, "baseline_loop_a_only")
    vlm = _load(results_dir, "loop_a_plus_vlm")
    reasoner = _load(results_dir, "loop_a_plus_vlm_plus_reasoner")
    full_chain = _load(results_dir, "full_chain")
    sweep = _load(results_dir, "trigger_frequency_sweep")
    multi2 = _load(results_dir, "multi_session_2")
    multi3 = _load(results_dir, "multi_session_3")
    warm_cold = _load(results_dir, "warm_cold")
    stage_diag = _load(results_dir, "loop_a_stage_diagnostic")
    admission_compare = _load(results_dir, "admission_queue_old_vs_new")

    lines: list[str] = []
    lines.append("# Sprint-0 CPU / Concurrency / Ollama Load Test Report")
    lines.append("")
    lines.append("Measurement-only phase. No production threshold, formula, or architecture was changed by this benchmark or its findings.")
    lines.append("")

    lines.append("# Executive Summary")
    if baseline:
        lines.append(
            f"- Loop-A baseline (no semantic load): {_fmt(baseline['loop_a']['loop_a_effective_fps'])} FPS "
            f"over {baseline['video']['frame_count']} real frames."
        )
    if vlm:
        degradation = compute_degradation_percent(
            baseline["loop_a"]["loop_a_effective_fps"] if baseline else None,
            vlm["loop_a"]["loop_a_effective_fps"],
        )
        lines.append(f"- Loop-A throughput under real VLM contention: {_fmt(vlm['loop_a']['loop_a_effective_fps'])} FPS (degradation {_fmt(degradation, '%')}).")
    if reasoner:
        degradation = compute_degradation_percent(
            baseline["loop_a"]["loop_a_effective_fps"] if baseline else None,
            reasoner["loop_a"]["loop_a_effective_fps"],
        )
        lines.append(f"- Loop-A throughput under real VLM+Reasoner contention: {_fmt(reasoner['loop_a']['loop_a_effective_fps'])} FPS (degradation {_fmt(degradation, '%')}).")
    lines.append("")

    lines.append("# Hardware / Environment")
    env = (baseline or vlm or reasoner or full_chain or {}).get("environment")
    if env:
        for key, value in env.items():
            lines.append(f"- **{key}**: {value}")
    else:
        lines.append("NOT RUN — no scenario produced environment metadata.")
    lines.append("")

    lines.append("# Loop-A Baseline")
    if baseline:
        la = baseline["loop_a"]
        lines.append(f"- Video: `{baseline['video']['path']}` ({baseline['video']['duration_seconds']:.1f}s, {baseline['video']['frame_count']} frames, sha256={baseline['video']['sha256'][:16]}...)")
        lines.append(f"- Wall-clock: {_fmt(la['loop_a_wall_seconds'], 's')}, effective FPS: {_fmt(la['loop_a_effective_fps'])}")
        lines.append("")
        lines.append("| Scenario | FPS | mean frame | p95 frame | p99 frame | max frame |")
        lines.append("|---|---|---|---|---|---|")
        lines.append(_loop_a_table_row("BASELINE_LOOP_A_ONLY", la))
        lines.append("")
        res = baseline.get("resources", {})
        lines.append(f"- Backend CPU%: mean={_fmt(res.get('backend_cpu_percent_mean'))} max={_fmt(res.get('backend_cpu_percent_max'))}; RSS: mean={_fmt(res.get('backend_rss_mb_mean'), ' MB')} peak={_fmt(res.get('backend_rss_mb_peak'), ' MB')}")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Loop-A + VLM")
    if vlm:
        la = vlm["loop_a"]
        lines.append("| Scenario | FPS | mean frame | p95 frame | p99 frame | max frame |")
        lines.append("|---|---|---|---|---|---|")
        if baseline:
            lines.append(_loop_a_table_row("BASELINE_LOOP_A_ONLY", baseline["loop_a"]))
        lines.append(_loop_a_table_row("LOOP_A_PLUS_VLM", la))
        lines.append("")
        vlm_stats = la["semantic_by_stage"].get("VLM", {})
        lines.append(f"- VLM calls attempted={la['failures']['attempted']} successful={la['failures']['successful']} failed={la['failures']['failed']} timed_out={la['failures']['timed_out']} dropped={la['trigger_dropped_by_semaphore']}")
        lines.append(f"- VLM latency: mean={_fmt(vlm_stats.get('mean'), 's')} p50={_fmt(vlm_stats.get('p50'), 's')} p95={_fmt(vlm_stats.get('p95'), 's')} max={_fmt(vlm_stats.get('max'), 's')} (n={vlm_stats.get('count', 0)})")
        lines.append(f"- Trigger attempts: {la['trigger_attempts']}")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Loop-A + VLM + Reasoner")
    if reasoner:
        la = reasoner["loop_a"]
        lines.append("| Scenario | FPS | mean frame | p95 frame | p99 frame | max frame |")
        lines.append("|---|---|---|---|---|---|")
        if baseline:
            lines.append(_loop_a_table_row("BASELINE_LOOP_A_ONLY", baseline["loop_a"]))
        lines.append(_loop_a_table_row("LOOP_A_PLUS_VLM_PLUS_REASONER", la))
        lines.append("")
        for stage in ("VLM", "REASONER"):
            s = la["semantic_by_stage"].get(stage, {})
            lines.append(f"- {stage} latency: mean={_fmt(s.get('mean'), 's')} p50={_fmt(s.get('p50'), 's')} max={_fmt(s.get('max'), 's')} (n={s.get('count', 0)})")
        lines.append(f"- Failures: {la['failures']}")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Full Semantic Chain")
    if full_chain:
        lines.append(f"- session_id={full_chain['session_id']} final_status={full_chain['final_session_status']}")
        lines.append(f"- Total wall-clock: {_fmt(full_chain['total_wall_seconds'], 's')}")
        lines.append(f"- Evidence packages: {full_chain['evidence_package_count']}, decisions: {full_chain['decision_count']} ({full_chain['decision_outcomes']})")
        lines.append(f"- Incidents created: {full_chain['incident_count']}")
        lines.append(f"- Verifier invoked: {full_chain['verifier_invoked']} (count={full_chain['verifier_invocation_count']})")
        if not full_chain["verifier_invoked"]:
            lines.append("  - Verifier was NOT naturally reached during this real run — reported honestly, not forced.")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Trigger-Frequency Sweep")
    if sweep:
        lines.append("| Level | interval(s) | trigger attempts | dropped | VLM calls (n) | Reasoner calls (n) | Loop-A FPS |")
        lines.append("|---|---|---|---|---|---|---|")
        for level, data in sweep["levels"].items():
            la = data["loop_a"]
            vlm_n = la["semantic_by_stage"].get("VLM", {}).get("count", 0)
            reasoner_n = la["semantic_by_stage"].get("REASONER", {}).get("count", 0)
            lines.append(f"| {level} | {data['trigger_interval_seconds']} | {la['trigger_attempts']} | {la['trigger_dropped_by_semaphore']} | {vlm_n} | {reasoner_n} | {_fmt(la['loop_a_effective_fps'])} |")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Multi-Session Contention")
    for label, data in (("2 concurrent sessions", multi2), ("3 concurrent sessions", multi3)):
        lines.append(f"## {label}")
        if data:
            lines.append(f"- Total wall-clock: {_fmt(data['total_wall_seconds'], 's')}")
            res = data.get("resources", {})
            lines.append(f"- Backend CPU%: mean={_fmt(res.get('backend_cpu_percent_mean'))} max={_fmt(res.get('backend_cpu_percent_max'))}")
            lines.append(f"- Ollama CPU%: mean={_fmt(res.get('ollama_cpu_percent_mean'))} max={_fmt(res.get('ollama_cpu_percent_max'))}")
            for s in data["per_session"]:
                lines.append(f"  - {s['session_id']}: status={s['final_status']} evidence_packages={s['evidence_packages']} decisions={s['decisions']}")
        else:
            lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Ollama Warm / Cold")
    if warm_cold:
        for label, data in warm_cold["results"].items():
            lines.append(f"## {label} ({data['model']})")
            lines.append(f"- Residency after forced unload (keep_alive=0): {data['residency_after_forced_unload']}")
            lines.append(f"- Cold call: {_fmt(data['cold_call']['latency_seconds'], 's')} (load_duration_ns={data['cold_call']['load_duration_ns']})")
            lines.append(f"- Residency after cold call: {data['residency_after_cold_call']}")
            lines.append(f"- Warm call (immediately after): {_fmt(data['warm_call']['latency_seconds'], 's')}")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Loop-A Stage-Level Tail-Latency Diagnostic (Semantic Admission Control phase)")
    if stage_diag:
        la = stage_diag["loop_a"]
        lines.append(f"- Whole-frame latency (as previously measured): mean={_fmt(la['frame_latency'].get('mean'), 's', 4)} max={_fmt(la['frame_latency'].get('max'), 's')}")
        lines.append("")
        lines.append("| Stage | mean | p95 | p99 | max |")
        lines.append("|---|---|---|---|---|")
        for stage, s in stage_diag["stage_latency"].items():
            lines.append(f"| {stage} | {_fmt(s.get('mean'), 's', 4)} | {_fmt(s.get('p95'), 's', 4)} | {_fmt(s.get('p99'), 's', 4)} | {_fmt(s.get('max'), 's', 3)} |")
        lines.append("")
        lines.append(f"- post-detect stage-sum-of-means: {_fmt(stage_diag.get('post_detect_stage_sum_of_means'), 's', 4)} vs. whole-frame-latency mean: {_fmt(stage_diag.get('whole_frame_latency_mean_for_comparison'), 's', 4)} (should closely match — confirms no unaccounted-for gap)")
        lines.append(f"- {stage_diag.get('diagnostic_note', '')}")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# Semantic Admission Control — OLD (drop-on-cap) vs NEW (bounded queue)")
    if admission_compare:
        old = admission_compare.get("old_drop_on_cap")
        new = admission_compare.get("new_bounded_admission_queue")
        lines.append("## OLD (threading.Semaphore, drop-on-cap — from the prior trigger_frequency_sweep.json)")
        if old:
            lines.append("| Level | interval(s) | trigger attempts | dropped | Loop-A FPS |")
            lines.append("|---|---|---|---|---|")
            for level, data in old.items():
                la = data["loop_a"]
                lines.append(f"| {level} | {data['trigger_interval_seconds']} | {la['trigger_attempts']} | {la['trigger_dropped_by_semaphore']} | {_fmt(la['loop_a_effective_fps'])} |")
        else:
            lines.append("NOT AVAILABLE (trigger_frequency_sweep.json not found).")
        lines.append("")
        lines.append("## NEW (SemanticAdmissionQueue, bounded + freshness-aware — real AnalysisOrchestrator)")
        if new:
            lines.append("| Level | interval(s) | enqueued | dequeued(started) | dropped_capacity | dropped_stale | completed | queue_wait mean/max | evidence pkgs | decisions |")
            lines.append("|---|---|---|---|---|---|---|---|---|---|")
            for level, data in new.items():
                qm = data["semantic_queue_metrics"]
                lines.append(
                    f"| {level} | {data['trigger_interval_seconds']} | {qm['enqueued_total']} | {qm['dequeued_total']} | "
                    f"{qm['dropped_capacity_total']} | {qm['dropped_stale_total']} | {qm['completed_total']} | "
                    f"{_fmt(qm.get('queue_wait_seconds_mean'), 's')}/{_fmt(qm.get('queue_wait_seconds_max'), 's')} | "
                    f"{data['evidence_package_count']} | {data['decision_count']} |"
                )
        else:
            lines.append("NOT RUN.")
    else:
        lines.append("NOT RUN.")
    lines.append("")

    lines.append("# CPU Attribution")
    lines.append("Backend vs. Ollama CPU% sampled every 0.5s via `psutil` (see each scenario's own `resources` block above) — Ollama's own process(es), matched by name, are attributed separately from the CrowdShield backend process.")
    lines.append("")

    lines.append("# Memory")
    lines.append("See each scenario's `resources.backend_rss_mb_*`/`resources.ollama_rss_mb_*` fields above.")
    lines.append("")

    lines.append("# Timeout / Retry Analysis")
    for label, data in (("VLM", vlm), ("VLM+Reasoner", reasoner), ("Full chain", None)):
        if data:
            f = data["loop_a"]["failures"]
            lines.append(f"- {label}: attempted={f['attempted']} successful={f['successful']} failed={f['failed']} timed_out={f['timed_out']} dropped={f['dropped']} retried={f['retried']}")
            for rec in f["records"]:
                lines.append(f"  - FAILURE: stage={rec['stage']} type={rec['exception_type']} elapsed={rec['elapsed_seconds']:.1f}s retries={rec['retry_count']}")
    if sweep:
        for level, data in sweep["levels"].items():
            f = data["loop_a"]["failures"]
            lines.append(f"- Sweep/{level}: attempted={f['attempted']} successful={f['successful']} failed={f['failed']} timed_out={f['timed_out']} dropped={f['dropped']} retried={f['retried']}")
            for rec in f["records"]:
                lines.append(f"  - FAILURE: stage={rec['stage']} type={rec['exception_type']} elapsed={rec['elapsed_seconds']:.1f}s retries={rec['retry_count']}")
    lines.append("")

    lines.append("# Operational Envelope")
    lines.append("(Filled in manually from the measured numbers above in the final response — see the governing task's own Step 18 wording: only measured numbers are used, no capacity claim beyond what was actually observed.)")
    lines.append("")

    lines.append("# Bottlenecks")
    lines.append("(Ranked in the final response, from the measured evidence above.)")
    lines.append("")

    lines.append("# Optimization Candidates")
    lines.append("(Ranked in the final response, from the measured evidence above — no optimization was implemented this phase.)")
    lines.append("")

    lines.append("# Unresolved Questions")
    lines.append("(Listed in the final response.)")
    lines.append("")

    lines.append("# Reproduction Commands")
    lines.append("```")
    lines.append("python scripts/benchmark_sprint0_cpu.py baseline")
    lines.append("python scripts/benchmark_sprint0_cpu.py vlm")
    lines.append("python scripts/benchmark_sprint0_cpu.py reasoner")
    lines.append("python scripts/benchmark_sprint0_cpu.py full-chain")
    lines.append("python scripts/benchmark_sprint0_cpu.py sweep")
    lines.append("python scripts/benchmark_sprint0_cpu.py multi-session --session-count 2")
    lines.append("python scripts/benchmark_sprint0_cpu.py warm-cold")
    lines.append("python scripts/benchmark_sprint0_cpu.py stage-diagnostic")
    lines.append("python scripts/benchmark_sprint0_cpu.py admission-compare")
    lines.append("python scripts/generate_sprint0_report.py")
    lines.append("```")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--results-dir", type=str, default=str(REPO_ROOT / "benchmark_results"))
    parser.add_argument("--output", type=str, default=str(REPO_ROOT / "SPRINT0_CPU_LOAD_TEST_REPORT.md"))
    args = parser.parse_args()

    report = generate_report(Path(args.results_dir))
    Path(args.output).write_text(report, encoding="utf-8")
    print(f"Wrote {args.output}")


if __name__ == "__main__":
    main()
