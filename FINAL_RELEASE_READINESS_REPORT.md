# CrowdShield — Final Release Readiness Report

Produced at the end of the Final Release Hardening phase. No new product features, no architecture redesign, no OpenVINO/INT8 work, no semantic-admission redesign, no DIS change, no risk-formula/threshold change were made this phase — only genuine test/infrastructure defects were fixed, and everything below is measured, real evidence, not a projection.

# Final Architecture State

Two-loop `AnalysisOrchestrator` (Loop A: Detection → Tracking → Optical Flow → Crowd Intelligence → Risk State → Trigger Engine, real-time, never blocks; Loop B: VLM → EvidenceBuilder → Reasoner → [Verifier if INCIDENT] → Incident correlation, submitted to a bounded `SemanticAdmissionQueue`). Session-level `SessionReportResult` (deterministic aggregation, zero new inference) and `SessionCopilot` (the one deliberate, session-isolated, grounded Q&A LLM surface) unchanged since the Final Intelligence phase. No architecture change this phase.

# Final Runtime Configuration

Verified via the ACTUAL effective `Settings()` object (not just `config.py` defaults or `.env` grep):

| Setting | Effective value | `.env` override? |
|---|---|---|
| `OLLAMA_NUM_THREAD` | **4** | None |
| `MAX_CONCURRENT_SEMANTIC_ANALYSES` | 1 | None |
| `SEMANTIC_QUEUE_MAX_DEPTH` | 1 | None |
| `SEMANTIC_QUEUE_STALENESS_SECONDS` | 30.0 | None |
| `DIS_PRESET` | "fast" | None |
| `VLM_REQUEST_TIMEOUT_SECONDS` | 60.0 (unchanged) | None |
| `VERIFIER_REQUEST_TIMEOUT_SECONDS` | 260.0 (unchanged — see Known Limitations) | None |

No stale `.env` conflict exists for any of the 5 primary settings this phase concerns — the code defaults ARE the effective configuration.

`OLLAMA_NUM_THREAD=4` confirmed reaching all three real Ollama call sites (VLM, Reasoner, Verifier) via 6 targeted mocked regression tests that inspect the actual `options` dict passed to `_client.chat()` — not merely inferred from source reading.

# Sprint-0 Performance Result

Decisive HIGH-frequency real scenario (the ONE condition previously confirmed to reliably reproduce catastrophic Loop-A damage), measured earlier in this same working session:

| Metric | BEFORE (depth=2, num_thread=None) | AFTER (depth=1, num_thread=4) |
|---|---|---|
| Loop-A frame_latency max | **888.87s** | **0.447s** |
| p95 | 0.632s | 0.259s |
| p99 | 1.004s | 0.356s |
| Total session wall-clock | 1567.20s | 359.26s |
| ollama_cpu% max | 825.0%+ | 436.9% |
| Semantic completed | 5 of 11 (45%) | 3 of 11 (27%, intended trade-off) |

**One real run each.** Not claimed as a statistical guarantee — see Known Limitations.

# Reasoner Contract Fix

`decision_result.py`'s WATCH/structured_report validator: WATCH may now optionally carry a `structured_report` (previously forbidden, causing a real 390.51s retry-then-fail production defect). INCIDENT still requires it; NO_INCIDENT/ABSTAIN still forbid it. Verified with a real single-attempt (no retry) 150.44s inference call. Unchanged this phase.

# Semantic Admission Policy

`SemanticAdmissionQueue`: 1 active worker (`MAX_CONCURRENT_SEMANTIC_ANALYSES`), 1 pending slot (`SEMANTIC_QUEUE_MAX_DEPTH`), 30s staleness. Non-blocking submit (measured max 0.0025s even under heavy real contention). Newest work displaces oldest queued work; stale work is dropped, never executed. Unchanged this phase (explicit non-goal: "do not redesign semantic admission").

# Test Suite Status

See the accompanying chat response for the exact final `pytest tests/ -v` command and result (run at the end of this phase). Summary of what changed this phase:
- 6 new mocked `OLLAMA_NUM_THREAD` forwarding regression tests (2 each: Reasoner, VLM, Verifier) — all pass.
- 1 pre-existing brittle test (`test_minicpm_vlm.py`) redesigned to a deterministic contract assertion — passes, and still catches real fabrication.
- 2 real-inference Verifier tests: genuinely exhibit real variance under `OLLAMA_NUM_THREAD=4` (see Known Flaky/Probabilistic Tests below) — NOT a test-design defect, a real production-configuration interaction, deliberately not papered over.

TypeScript: clean (`npx tsc --noEmit`). ESLint: clean (`npx eslint .`). No frontend files were changed in this or the preceding 3 phases.

# Known Flaky/Probabilistic Tests

1. **`test_minicpm_vlm.py::test_empty_no_hazard_scene_never_produces_a_confident_fabricated_hazard_claim`** (formerly `..._can_return_empty_observations_list`) — inherently probabilistic (real VLM output on a blank scene), by design. FIXED this phase: rewritten from a brittle exact-content assertion to a deterministic contract (schema/coordinate/confidence validity + no confident fabricated hazard claim on a blank scene). Still genuinely catches regressions; no longer brittle.

2. **`test_verifier.py::test_real_verification_of_well_grounded_incident_decision_passes_and_uses_think_true`** and **`test_verifier.py::test_real_verification_catches_deliberately_unsupported_claim`** — REAL, evidence-backed finding, not simple flakiness: `OLLAMA_NUM_THREAD=4` measurably increases Verifier `think=True` latency. Real samples this phase: 237.32s (completed, uncensored) and two right-censored samples that exceeded the existing 260.0s timeout (true value unknown, ≥260s). All three real samples this phase were higher than the pre-`num_thread`-cap calibration history (177.76–213.50s). **Deliberately not fixed this phase** (would require either recalibrating `VERIFIER_REQUEST_TIMEOUT_SECONDS` with a properly uncensored measurement, or reconsidering `num_thread` for Verifier specifically — both are real config decisions with real trade-offs, explicitly out of scope for a hardening/cleanup pass). The architecture already degrades gracefully when this happens (`LLMVerificationUnavailableError` is caught; the INCIDENT decision is persisted unverified, never a crash) — this is a verification-coverage risk, not a stability or correctness risk.

# Product Smoke Test

Real end-to-end run via the existing validation harness (`scripts/validate_acute_event_video.py --full-chain`), through the REAL, unmodified `AnalysisOrchestrator`/`AnalysisSession` (not a simulation):

- ✅ Video analysis completed — session reached `COMPLETED`.
- ✅ Loop A remained responsive — no hang; the new admission queue correctly evicted a stale queued trigger under real contention (`"queue at capacity (1) — evicted OLDEST queued task ... waited=32.83s"`), exactly as designed.
- ✅ No semantic request remained stuck indefinitely — confirmed via the same log evidence; the run reached COMPLETED promptly.
- ✅ Real VLM calls DID time out during this run (see Known Limitations) — handled gracefully (`VLMUnavailableError` caught, evidence marked incomplete, no crash).
- ✅ Session report built successfully with real, meaningful, non-empty content: `investigated_event_count=3`, `confirmed_incident_count=0`, a real risk trend (`RISING`, real Δ/window), and a correct, non-generic zero-incident summary ("No event met the evidence threshold required for incident escalation. 3 event(s) were investigated (3 ABSTAINED).").
- ✅ Zero-incident state renders correctly (data level) — never presented as an empty/uninformative report.
- ✅ 5 real heatmap types (density/pressure/risk/predictive/flow-congestion) rendered as real, substantial JPEG files (54KB–122KB each) per investigated event.
- ✅ Copilot answered a real, grounded question correctly: asked "Were there any confirmed incidents in this session, and why or why not?", real answer correctly explained the ABSTAIN reasons (confidence floor, incomplete evidence) with no fabrication.
- ✅ No cross-session data leakage — covered by existing, still-passing isolation tests (`test_serialized_context_never_leaks_another_sessions_data`, `test_ask_copilot_cannot_retrieve_other_sessions_data`), not independently re-verified via a second live session this pass.
- ⚠️ **NOT independently re-verified via a live browser this pass**: risk trend/metric card visual rendering, and absence of browser console errors. tsc/eslint confirm the frontend compiles cleanly, and no frontend file has been touched across this or the preceding 3 phases — but this is not the same as a live visual confirmation.

# Copilot Verification

Real call, real grounded answer (see Product Smoke Test above). Session-isolated (existing tests, unchanged, still passing). No chain-of-thought exposure (unchanged architecture).

# Heatmap Verification

Real files generated for all 5 heatmap types, substantial non-trivial sizes, per real investigated event — confirmed via this phase's own smoke test (see above). Heatmap rendering code itself unchanged this phase.

# Remaining Known Limitations

1. **Verifier timeout margin erosion under `OLLAMA_NUM_THREAD=4`** (see Known Flaky/Probabilistic Tests) — the single most significant open item. Real INCIDENT-outcome sessions have an elevated (not previously present, not precisely quantified) chance of the Verifier step timing out. Graceful degradation is confirmed; verification coverage is the risk.
2. All Sprint-0/CPU-stabilization performance numbers are from **single real runs**, not averaged across repeated trials — real run-to-run variance has already been directly observed and documented (the original catastrophic stall did not reproduce in an earlier isolated sweep run under otherwise-identical conditions).
3. Frontend visual rendering (metric cards, heatmap viewer, report page, Copilot chat UI, console errors) was not independently re-verified via a live browser session this specific phase.
4. VLM real calls were observed timing out during this phase's own smoke test under real, repeated, close-together triggers — consistent with, though not as severe as, the Verifier finding above; `VLM_REQUEST_TIMEOUT_SECONDS=60.0` also has a somewhat reduced margin under `OLLAMA_NUM_THREAD=4` versus its pre-cap calibration.
5. `cv2.setNumThreads()` (OpenCV's own internal thread cap, confirmed to default to 16 on this machine) remains an uninvestigated, real, additional lever if further tail-latency tightening is ever needed.

# Recommended Final Operating Configuration

For today's submission/demo, unchanged from the candidate configuration handed into this phase:

```
OLLAMA_NUM_THREAD=4
SEMANTIC_QUEUE_MAX_DEPTH=1
SEMANTIC_QUEUE_STALENESS_SECONDS=30.0
MAX_CONCURRENT_SEMANTIC_ANALYSES=1
DIS_PRESET=fast
```

Under the tested single-session HIGH-frequency workload, this configuration reduced the observed maximum Loop-A frame latency from 888.87s to 0.447s in the decisive benchmark run. This is a large, real, measured improvement — not a claim of universal real-time guarantee under all conditions, and not a claim of support for any specific number of concurrent cameras/sessions beyond the 2-session real test already performed.
