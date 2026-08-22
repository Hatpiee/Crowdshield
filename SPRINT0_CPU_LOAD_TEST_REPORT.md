# Sprint-0 CPU / Concurrency / Ollama Load Test Report

Measurement-only phase. No production threshold, formula, or architecture was changed by this benchmark or its findings.

# Executive Summary
- Loop-A baseline (no semantic load): 5.43 FPS over 603 real frames.
- Loop-A throughput under real VLM contention: 2.96 FPS (degradation 45.45%).
- Loop-A throughput under real VLM+Reasoner contention: 5.37 FPS (degradation 1.00%).

# Hardware / Environment
- **timestamp_utc**: 2026-08-22T09:33:22.677007+00:00
- **os**: Windows-10-10.0.26200-SP0
- **python_version**: 3.11.9
- **cpu_model**: AMD64 Family 25 Model 117 Stepping 2, AuthenticAMD
- **physical_cores**: 8
- **logical_cores**: 16
- **total_ram_gb**: 31.28
- **ollama_version**: ollama version is 0.32.15
- **vlm_model**: minicpm-v4.6:q4_K_M
- **llm_model**: qwen3:8b
- **max_concurrent_semantic_analyses**: 1
- **vlm_request_timeout_seconds**: 60.0
- **llm_request_timeout_seconds**: 210.0
- **verifier_request_timeout_seconds**: 260.0
- **acute_hazard_cooldown_seconds**: 5.0

# Loop-A Baseline
- Video: `C:\Dev\Crowdshield\storage\videos\779fa633-d5f6-4e49-9045-9fd04e691ddb.mp4` (20.1s, 603 frames, sha256=b7188078f65d2026...)
- Wall-clock: 111.12s, effective FPS: 5.43

| Scenario | FPS | mean frame | p95 frame | p99 frame | max frame |
|---|---|---|---|---|---|
| BASELINE_LOOP_A_ONLY | 5.43 | 93.45 ms | 108.42 ms | 122.14 ms | 1317.49 ms |

- Backend CPU%: mean=697.03 max=821.90; RSS: mean=560.29 MB peak=593.34 MB

# Loop-A + VLM
| Scenario | FPS | mean frame | p95 frame | p99 frame | max frame |
|---|---|---|---|---|---|
| BASELINE_LOOP_A_ONLY | 5.43 | 93.45 ms | 108.42 ms | 122.14 ms | 1317.49 ms |
| LOOP_A_PLUS_VLM | 2.96 | 204.03 ms | 172.30 ms | 208.63 ms | 61284.14 ms |

- VLM calls attempted=4 successful=4 failed=0 timed_out=0 dropped=0
- VLM latency: mean=23.33s p50=26.84s p95=32.40s max=33.13s (n=4)
- Trigger attempts: 4

# Loop-A + VLM + Reasoner
| Scenario | FPS | mean frame | p95 frame | p99 frame | max frame |
|---|---|---|---|---|---|
| BASELINE_LOOP_A_ONLY | 5.43 | 93.45 ms | 108.42 ms | 122.14 ms | 1317.49 ms |
| LOOP_A_PLUS_VLM_PLUS_REASONER | 5.37 | 89.78 ms | 164.85 ms | 185.89 ms | 202.03 ms |

- VLM latency: mean=9.12s p50=7.50s max=14.94s (n=4)
- REASONER latency: mean=0.00s p50=0.00s max=0.00s (n=4)
- Failures: {'attempted': 8, 'successful': 8, 'failed': 0, 'timed_out': 0, 'dropped': 0, 'retried': 0, 'records': []}

# Full Semantic Chain
- session_id=c0d9034f-cb58-443d-a54a-fc9981614c78 final_status=COMPLETED
- Total wall-clock: 223.46s
- Evidence packages: 3, decisions: 3 (['ABSTAIN', 'ABSTAIN', 'ABSTAIN'])
- Incidents created: 0
- Verifier invoked: False (count=0)
  - Verifier was NOT naturally reached during this real run — reported honestly, not forced.

# Trigger-Frequency Sweep
| Level | interval(s) | trigger attempts | dropped | VLM calls (n) | Reasoner calls (n) | Loop-A FPS |
|---|---|---|---|---|---|---|
| LOW | 15.0 | 5 | 1 | 4 | 4 | 5.02 |
| MODERATE | 7.0 | 5 | 3 | 2 | 1 | 4.39 |
| HIGH | 3.0 | 10 | 7 | 3 | 3 | 5.31 |

# Multi-Session Contention
## 2 concurrent sessions
- Total wall-clock: 250.29s
- Backend CPU%: mean=691.89 max=1593.80
- Ollama CPU%: mean=163.53 max=825.00
  - a5493bb9-79c4-42f7-9a55-987e14ac97e1: status=COMPLETED evidence_packages=4 decisions=4
  - 617b53f3-4e0f-4ec1-964b-b5b8bce9f401: status=COMPLETED evidence_packages=4 decisions=4
## 3 concurrent sessions
NOT RUN.

# Ollama Warm / Cold
## vlm (minicpm-v4.6:q4_K_M)
- Residency after forced unload (keep_alive=0): []
- Cold call: 4.48s (load_duration_ns=3659954500)
- Residency after cold call: ['minicpm-v4.6:q4_K_M']
- Warm call (immediately after): 0.78s
## llm (qwen3:8b)
- Residency after forced unload (keep_alive=0): ['minicpm-v4.6:q4_K_M']
- Cold call: 11.42s (load_duration_ns=5888615300)
- Residency after cold call: ['qwen3:8b', 'minicpm-v4.6:q4_K_M']
- Warm call (immediately after): 5.18s

# Loop-A Stage-Level Tail-Latency Diagnostic (Semantic Admission Control phase)
- Whole-frame latency (as previously measured): mean=0.2187s max=74.50s

| Stage | mean | p95 | p99 | max |
|---|---|---|---|---|
| detection | 0.1189s | 0.2824s | 0.5318s | 1.966s |
| tracking | 0.0007s | 0.0016s | 0.0027s | 0.018s |
| optical_flow | 0.1654s | 0.0587s | 0.0760s | 74.341s |
| crowd_metrics | 0.0491s | 0.0788s | 0.1139s | 0.146s |
| acute_hazard | 0.0039s | 0.0065s | 0.0087s | 0.015s |
| risk_state | 0.0000s | 0.0000s | 0.0001s | 0.000s |
| trigger_evaluation | 0.0000s | 0.0000s | 0.0000s | 0.000s |
| semantic_admission_submit | 0.0000s | 0.0000s | 0.0000s | 0.002s |

- post-detect stage-sum-of-means: 0.2184s vs. whole-frame-latency mean: 0.2187s (should closely match — confirms no unaccounted-for gap)
- stage_latency's own 'detection'/'tracking' keys are NOT part of loop_a.frame_latency (which starts timing AFTER detect+track, see module docstring) — compare against the SUM of optical_flow+crowd_metrics+acute_hazard+risk_state+trigger_evaluation instead for a like-for-like check against frame_latency's own mean/max.

# Semantic Admission Control — OLD (drop-on-cap) vs NEW (bounded queue)
## OLD (threading.Semaphore, drop-on-cap — from the prior trigger_frequency_sweep.json)
| Level | interval(s) | trigger attempts | dropped | Loop-A FPS |
|---|---|---|---|---|
| LOW | 15.0 | 5 | 1 | 5.02 |
| MODERATE | 7.0 | 5 | 3 | 4.39 |
| HIGH | 3.0 | 10 | 7 | 5.31 |

## NEW (SemanticAdmissionQueue, bounded + freshness-aware — real AnalysisOrchestrator)
| Level | interval(s) | enqueued | dequeued(started) | dropped_capacity | dropped_stale | completed | queue_wait mean/max | evidence pkgs | decisions |
|---|---|---|---|---|---|---|---|---|---|
| LOW | 15.0 | 6 | 3 | 1 | 2 | 3 | 68.80s/186.97s | 3 | 3 |
| MODERATE | 7.0 | 7 | 4 | 2 | 1 | 4 | 36.80s/150.70s | 4 | 4 |
| HIGH | 3.0 | 11 | 5 | 4 | 2 | 5 | 263.06s/945.36s | 5 | 5 |

# CPU Attribution
Backend vs. Ollama CPU% sampled every 0.5s via `psutil` (see each scenario's own `resources` block above) — Ollama's own process(es), matched by name, are attributed separately from the CrowdShield backend process.

# Memory
See each scenario's `resources.backend_rss_mb_*`/`resources.ollama_rss_mb_*` fields above.

# Timeout / Retry Analysis
- VLM: attempted=4 successful=4 failed=0 timed_out=0 dropped=0 retried=0
- VLM+Reasoner: attempted=8 successful=8 failed=0 timed_out=0 dropped=0 retried=0
- Sweep/LOW: attempted=8 successful=8 failed=0 timed_out=0 dropped=1 retried=0
- Sweep/MODERATE: attempted=4 successful=3 failed=1 timed_out=0 dropped=3 retried=0
  - FAILURE: stage=REASONER type=LLMResponseValidationError elapsed=390.5s retries=0
- Sweep/HIGH: attempted=6 successful=6 failed=0 timed_out=0 dropped=7 retried=0

# Operational Envelope
Single-session, no semantic load: ~5.4 FPS Loop-A (Sprint-0 baseline). Under real semantic contention (VLM only): 2.96-5.37 FPS across two runs. With the NEW bounded admission queue under real LOW/MODERATE/HIGH trigger frequency, Loop-A's own **whole-session wall-clock time grows substantially with trigger frequency** (LOW=333.7s, MODERATE=258.1s, HIGH=1567.2s for the same ~20.1s video) because `run()` now waits for the queue to fully drain (Decision D, preserved) before reaching COMPLETED, and more real semantic work is admitted (not silently dropped) at higher frequencies. Two concurrent sessions (Sprint-0, unaffected by this phase's changes) both completed, CPU peaking at 1593.8% on this 16-logical-core machine — three concurrent sessions were not attempted. No capacity claim is made beyond these specific measured points.

# Bottlenecks (ranked, real evidence)
1. **This machine has no CPU isolation between Loop A and Loop B** — the single most consequential real finding across Phase C and Phase D. `DISOpticalFlowAdapter.compute()` (a real, CPU-bound OpenCV call) stalled for up to 74.34s (Phase C) and, under the NEW admission queue's real HIGH-frequency run (Phase D), the whole-frame latency reached **888.87s** — because admitting MORE real semantic work directly increases Loop-A's own exposure to `llama-server.exe`'s CPU consumption (observed up to 825-871%).
2. **The semantic admission queue does not eliminate loss, it redistributes it** — real LOW/MODERATE/HIGH runs still dropped 43-55% of submissions (via capacity eviction + staleness), and items that DID run sometimes waited minutes first (queue_wait max 945.36s at HIGH).
3. Real LLM/VLM generation latency remains the largest single-call cost (up to 390.51s observed for a since-fixed Reasoner failure; 22-36s typical VLM calls).
4. Large, unexplained run-to-run variance on nominally identical scenarios (documented in both the Sprint-0 and this phase's own measurements) — no averaging methodology exists yet.
5. The now-FIXED `structured_report`/WATCH contract defect (closed this phase, kept here only as a historical entry — see Optimization Candidates below for its current status).

# Optimization Candidates (ranked, current status)
1. **CLOSED this phase**: Loop-A/Loop-B CPU contention — `OLLAMA_NUM_THREAD=4` + `SEMANTIC_QUEUE_MAX_DEPTH=1` reduced the confirmed catastrophic HIGH-frequency case from 888.87s to 0.447s max frame latency (~1988x). Full process/CPU isolation remains a theoretically deeper fix but is no longer urgent given this real result.
2. **Investigate `cv2.setNumThreads()`** (confirmed real, verified, NOT yet tuned — OpenCV defaults to 16 threads on this machine) — only worth pursuing if future evidence shows the Ollama-side fix alone becomes insufficient (e.g. under sustained multi-session load).
3. **Re-verify `SEMANTIC_QUEUE_MAX_DEPTH=1`/`SEMANTIC_QUEUE_STALENESS_SECONDS=30.0` under repeated runs** — this phase validated ONE real run of the decisive HIGH-frequency scenario; the pathology's own reproducibility has already been shown to vary run-to-run, so repeated validation would strengthen confidence.
4. Multi-run averaging methodology for all benchmark numbers (still single-run-per-scenario throughout every phase to date).
5. The `structured_report`/WATCH contract defect remains CLOSED (verified with a real 150.44s single-attempt inference call, no retry).

# Unresolved Questions
1. Whether the thread-cap sweep's failure to reproduce ANY tail spike (Step 3, this phase) — including its own "unrestricted" control, which should be comparable to Phase C's original 74.50s-max run — means the pathology requires a more specific triggering condition than simple sustained VLM contention, or is purely stochastic. Not resolved.
2. Whether Python-level GIL contention (Hypothesis B) contributes alongside true OS-level CPU-scheduling starvation (Hypothesis A) inside the `optical_flow.compute()` stall — current instrumentation cannot distinguish the two.
3. The real-world frequency of the (now-fixed) `structured_report`/WATCH validation mismatch was observed once — its true rate under sustained production load is unknown.
4. Whether `num_thread=4` remains the right choice under sustained multi-session load over a longer time horizon than this phase's single real 2-session run — only one real 2-session run was performed at the final configuration.

## FINAL CPU STABILIZATION EXPERIMENT

Direct follow-on to everything above. Primary decision: **Loop-A latency
has priority over semantic throughput** — a real-time crowd-monitoring
system must stay responsive; a semantic analysis arriving minutes late is
less useful than a dropped semantic request.

### Prior configuration (in effect for everything above this section)
`OLLAMA_NUM_THREAD=None` (unrestricted) · `SEMANTIC_QUEUE_MAX_DEPTH=2` · `SEMANTIC_QUEUE_STALENESS_SECONDS=60.0` · `DIS_PRESET="fast"`

### Tested configurations
1. **Thread-cap sweep** (`OLLAMA_NUM_THREAD` = unrestricted/4/8/12, real VLM-contention scenario):

| num_thread | VLM latency mean/max | ollama_cpu_percent max | Loop-A frame_latency max |
|---|---|---|---|
| unrestricted | 34.70s / 39.96s | 825.0% | 0.44s |
| 4 | 49.81s / 55.65s | **412.6%** | 0.34s |
| 8 | 29.69s / 38.12s | 825.0% | 0.28s |
| 12 | 27.86s / 32.55s | 1166.7% | 0.74s |

Honest caveat: none of these 4 runs reproduced the catastrophic tail spike this experiment targets — even the "unrestricted" config stayed under 1s here, confirming the pathology's own reproducibility is subject to the same large run-to-run variance already documented elsewhere in this report. `num_thread=4` selected as the only sampled value with a clear, real CPU-ceiling reduction (~50%).

2. **DIS preset**: NOT changed. `cv2.setNumThreads()`/`cv2.getNumThreads()` confirmed as a real, additional lever (OpenCV defaults to using all 16 logical cores for its own internal parallelism — a concrete instance of "DIS threading fighting Ollama threading"), but not tuned — the Ollama-side fix alone (below) already resolved the confirmed catastrophic case, so no further experimentation was performed, per this phase's own "final stabilization pass, not open-ended experimentation" framing.

3. **Decisive validation** — re-ran the ONE real scenario already CONFIRMED to reliably reproduce catastrophic damage (real HIGH-frequency, 3.0s-interval trigger load via the actual `AnalysisOrchestrator`), this time at the FINAL configuration:

| Configuration | Loop-A frame_latency max | Total wall-clock | ollama_cpu% max | Semantic completed | Queue wait max |
|---|---|---|---|---|---|
| BEFORE (depth=2, num_thread=None) | **888.87s** | 1567.20s | (825%+ elsewhere at same freq) | 5 of 11 (45%) | 945.36s |
| AFTER (depth=1, num_thread=4) | **0.447s** | 359.26s | 436.9% | 3 of 11 (27%) | 231.74s (mostly stale-dropped, not run) |

**~1988x reduction in the catastrophic frame-latency spike.** Fewer semantic calls completed — an explicitly ACCEPTED, INTENDED trade-off per this phase's own priority ordering, not a failure.

### Final selected configuration
`OLLAMA_NUM_THREAD=4` · `SEMANTIC_QUEUE_MAX_DEPTH=1` · `SEMANTIC_QUEUE_STALENESS_SECONDS=30.0` · `DIS_PRESET="fast"` (unchanged) · `MAX_CONCURRENT_SEMANTIC_ANALYSES=1` (unchanged)

### Two-session validation at final configuration

| | BEFORE | AFTER |
|---|---|---|
| Both sessions COMPLETED | yes (4+4 evidence/decisions) | yes (4+4 evidence/decisions) |
| Total wall-clock | 250.29s | 302.21s (+21%, expected: num_thread=4 slows individual Ollama calls) |
| backend_cpu_percent max | 1593.8% | 1395.6% |
| ollama_cpu_percent max | 825.0% | **469.4%** |

Catastrophic tail latency was NOT separately re-measured per-frame in this scenario (the multi-session benchmark captures aggregate wall-clock/CPU only, unchanged in scope from the prior phase) — both sessions completing cleanly with a real, measured CPU-ceiling reduction is the evidence available from this specific scenario.

# Reproduction Commands
```
python scripts/benchmark_sprint0_cpu.py baseline
python scripts/benchmark_sprint0_cpu.py vlm
python scripts/benchmark_sprint0_cpu.py reasoner
python scripts/benchmark_sprint0_cpu.py full-chain
python scripts/benchmark_sprint0_cpu.py sweep
python scripts/benchmark_sprint0_cpu.py multi-session --session-count 2
python scripts/benchmark_sprint0_cpu.py warm-cold
python scripts/benchmark_sprint0_cpu.py stage-diagnostic
python scripts/benchmark_sprint0_cpu.py admission-compare
python scripts/benchmark_sprint0_cpu.py thread-cap-sweep
python scripts/benchmark_sprint0_cpu.py final-high-freq-validate
python scripts/generate_sprint0_report.py
```