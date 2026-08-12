# Engineering Judgment Log

This file tracks engineering-judgment decisions made *during implementation*
that are **not empirically validated** and are candidates for future
recalibration. It is distinct from the master spec's own frozen /
configurable / open-validation categorization — those are decisions the spec
itself already made. This log is for the narrower, in-code judgment calls
the spec left underdetermined (an unspecified constant, an unset tunable
default) that an implementer had to pick a real number for. It will be added
to as similar judgment calls come up in future phases.

| Decision | Phase | Current Value | Rationale | Validation Status |
|---|---|---|---|---|
| Box-to-point collapse constants (`HEAD_Y_FRACTION`, `HEAD_SCALE_FRACTION`) — `backend/app/pipeline/box_to_point.py` | 6 | `HEAD_Y_FRACTION=0.10`, `HEAD_SCALE_FRACTION=0.13` | Engineering/anthropometric approximation: `HEAD_Y_FRACTION` approximates the head's vertical center rather than the box's raw top edge (which often includes empty margin); `HEAD_SCALE_FRACTION` approximates head height as ~1/7–1/8 of standing body height. | Not peer-reviewed or empirically measured against real footage. Candidate for recalibration once real annotated footage is available. |
| `TRACKER_TRACK_ACTIVATION_THRESHOLD` default — `backend/app/core/config.py` | 7 | `0.3` | Aligns ByteTrack's new-track confirmation floor with `DETECTOR_CONFIDENCE_THRESHOLD`'s 0.25 floor (library's own general-purpose default of 0.7 was found, on real dense-crowd footage, to confirm tracks for almost no one, since most real detections in that footage never exceeded ~0.66 confidence). | Not empirically validated on real crowd footage — a deliberately-chosen value to unblock multi-person tracking, not a tuned one. Candidate for Sprint-0 validation (§35) recalibration, exactly like the master spec's own Crowd Pressure thresholds are documented as open for recalibration. |
| `DIS_PRESET` default — `backend/app/core/config.py` | 8 | `"fast"` | Balance between quality and CPU cost per master spec §5's own citation that DIS runs "~21-48 FPS depending on preset" and is the system's most probable CPU throughput ceiling. | Not empirically validated / benchmarked on this project's target hardware yet. Candidate for Sprint-0 validation (§35) recalibration. |
| `MOTION_MAGNITUDE_NOISE_FLOOR` default — `backend/app/core/config.py` | 8 | `0.5` (pixels) | Dense optical flow on low-texture/static regions produces small, essentially meaningless vectors from compression noise and lighting changes; pixels below this floor are excluded from this phase's whole-frame summary statistics only (never from the raw flow field itself). | Not empirically validated against real footage — a reasonable-sounding round number, not a measured threshold. Candidate for Sprint-0 validation (§35) recalibration. |
| `CROWD_GRID_CELL_SIZE_PX` default — `backend/app/core/config.py` | 9 | `40` (pixels per square cell edge) | The shared spatial grid Density and Flow are both computed on (so Crowd Pressure can combine them pointwise without interpolation) needs some cell size; 40px is a reasonable-looking round number for typical crowd-camera framing, nothing more. | Not calibrated against any real venue's physical scale — this project has no camera calibration (see "Known Structural Limitation" section below), so there is no principled way to relate 40px to a real distance yet. Candidate for Sprint-0 validation (§35) recalibration. |
| Density confidence-rule constants (`MIN_POINTS_FOR_RELIABLE_ESTIMATION=3`, `TOO_FEW_POINTS_CONFIDENCE=0.4`, `VORONOI_UNAVAILABLE_CONFIDENCE=0.85`, `HIGH_DISAGREEMENT_THRESHOLD=0.75`, `HIGH_DISAGREEMENT_CONFIDENCE=0.5`) — `backend/app/pipeline/density.py` | 9 | See values above | A simple, threshold-based rule (per decision #6's explicit instruction to keep this simple, not an elaborate model) mapping KDE/Voronoi failure modes and disagreement magnitude to a confidence value. | Not empirically validated. Worth flagging with real evidence: on real footage (`people_clip.mp4`, 149 frame-pairs), `high_voronoi_disagreement` fired on ~34% of frames and `too_few_points` on ~21% (both mostly during low-track-count spans of 2-7 people) — i.e. `estimation_confidence` was below 1.0 more often than not on this footage (mean 0.703 across the run). This isn't necessarily wrong (small-N density estimation genuinely IS less reliable, which is exactly what this mechanism is meant to signal), but `HIGH_DISAGREEMENT_THRESHOLD=0.75` in particular was picked without empirical grounding for how much KDE/Voronoi disagreement is "normal" at small N vs. genuinely concerning. Candidate for Sprint-0 validation (§35) recalibration once real annotated footage with known ground-truth crowd counts is available. |
| Degraded-path density grid uses a raw per-cell point count (`_histogram_fallback_grid`), NOT a KDE surface with confidence lowered — `backend/app/pipeline/density.py` | 9 | Unsmoothed histogram: each point increments exactly one grid cell by `1.0`, no spatial spread into neighboring cells | Not specified in the original prompt as its own decision; clarified on request. Whenever KDE cannot run at all (too few points) or numerically fails (`LinAlgError`, e.g. exactly-coincident points), `gaussian_kde` is never invoked — this is a structurally different computation path, not the same smoothed estimate with a lower `estimation_confidence` attached, since a KDE surface would misleadingly imply spatial confidence in point location that doesn't exist with this few points. | Not a numeric threshold to recalibrate — a structural implementation choice. Logged here only so a future reader doesn't assume the degraded grid is "KDE, just less trusted." |
| `DENSITY_CONGESTION_THRESHOLD` / `FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC` — `backend/app/core/config.py` | 10 | `0.1` people/cell, `40.0` px/s | Congestion requires density AND low flow magnitude, combined (frozen decision — density alone never triggers it). Informed by REAL observed values: re-running Phase 9's own preview script against `people_clip.mp4` (149 frame-pairs) plus a one-off probe of per-cell speed found occupied-cell (density > 0.01) density had p90=0.108 and stable, non-degraded multi-track frames typically peaked max_density ~0.11-0.18; occupied-cell speed had p25=28.0 px/s and whole-grid speed had p25=37.6 px/s. 0.1 and 40.0 sit just at/above these real percentiles. | Not empirically validated — informed by real observed distributions on ONE video, not tuned against labeled ground-truth "this was actually a congested queue" events. PIXEL-SPACE-native, same units limitation as Phase 9's own thresholds (see "Known Structural Limitation" below). Candidate for Sprint-0 validation (§35) recalibration. |
| `BOTTLENECK_WINDOW_FRAMES` — `backend/app/core/config.py` | 10 | `30` frames (~1s at `people_clip.mp4`'s 30fps) | Rolling window length for Bottleneck's tracer-advection convergence measurement — the spec's own suggested starting point (`~30`). No sensitivity analysis performed on how window length affects the convergence ratio's reliability. | Not empirically validated. A shorter window reacts faster but is noisier; a longer window is smoother but lets tracers drift further, compounding forward-Euler integration error (see "Known Design Tradeoff: Bottleneck's Simplified Lagrangian Method" below). Candidate for Sprint-0 validation (§35) recalibration. |
| Reverse Flow constants (`REVERSE_FLOW_BASELINE_EMA_ALPHA=0.1`, `REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS=15`, `REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES=120`, `REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES=10`, `REVERSE_FLOW_PERSISTENCE_MIN_COUNT=6`) — `backend/app/core/config.py` / `backend/app/pipeline/reverse_flow.py` | 10 | See values above | All five are the spec's own suggested defaults, not independently derived. Unlike Congestion's two thresholds, NONE of these five are pixel-space quantities — EMA alpha is a learning rate, the two frame/observation counts are plain integers, and the deviation threshold is an ANGLE (degrees) — so the Phase 9/Congestion pixel-space units disclosure does NOT apply to any of them. | Not empirically validated on real crowd footage. On `people_clip.mp4` (sparse, low-density) reverse flow was observed only briefly and marginally (frames 95-101, 1-3 of 252 cells flagged, fraction 0.004-0.012 — see the preview script's real run output) — too weak a signal to validate or invalidate these defaults either way. See "Known Design Tradeoff: Reverse Flow Mechanism Not Validated for Crowds" below. |
| `PRESSURE_SCORE_REFERENCE_PX` — `backend/app/core/config.py` | 11 | `100.0` (pixel-space, "people * pixels^2/second^2 per cell") | Risk Score's pressure sub-score is `min(100, max_pressure / PRESSURE_SCORE_REFERENCE_PX * 100)` — worst-case-sensitive by design (uses `max_pressure`, not `mean_pressure`; see "Known Design Choice: Pressure Uses max, Projection Uses mean" below). Informed by REAL observed values: re-running Phase 9's preview script against `people_clip.mp4` (149 frame-pairs) found `max_pressure` p25=2.58, median=14.89, p75=41.0, p90=73.4, p95=89.5, max=262.8. 100.0 sits just above the real p95, so only the top ~5% of observed frames (plus any footage with genuinely worse local crushing) saturate the sub-score at 100. | Not empirically validated — informed by real observed distribution on ONE sparse video, not tuned against labeled ground-truth crush events. PIXEL-SPACE-native, same units limitation as Phase 9/10's own thresholds. Candidate for Sprint-0 validation (§35) recalibration. |
| `RISK_SCORE_WEIGHT_PRESSURE=0.5` / `RISK_SCORE_WEIGHT_CONGESTION=0.2` / `RISK_SCORE_WEIGHT_BOTTLENECK=0.2` / `RISK_SCORE_WEIGHT_REVERSE_FLOW=0.1` — `backend/app/core/config.py` | 11 | See values above (validated to sum to 1.0 at settings load time) | Pressure gets the largest weight per §12's own framing as "the single most heavily validated decision in the entire project"; Congestion and Bottleneck are weighted equally as secondary signals; Reverse Flow gets the smallest weight as the least-validated mechanism (adapted from vehicle wrong-way-detection literature, explicitly flagged by the spec itself as needing pedestrian-domain validation). When a sub-score is unavailable (only Bottleneck can be), its weight is redistributed proportionally at runtime among the available sub-scores (`risk_score.py`'s `_redistribute_weights`) — these four configured values are never edited per-frame to simulate that. | Not empirically validated — no labeled "this frame was genuinely high-risk" ground truth exists yet to tune against. The 0.5/0.2/0.2/0.1 split itself, not just the individual numbers, is an engineering judgment call (see risk_score.py's Design Rationale docstring). Candidate for Sprint-0 validation (§35) recalibration. |
| `PREDICTIVE_WINDOW_SECONDS=10.0` / `PREDICTION_HORIZON_SECONDS=30.0` — `backend/app/core/config.py` | 11 | `10.0` / `30.0` seconds | Both are the spec's own suggested defaults for the lightweight linear-regression Crowd Pressure projection (`predictive_projection.py`). Time-based (not frame-count-based), so behavior is consistent across videos with different fps. | Not empirically validated. **Explicitly NOT the problem statement's "10 minutes before" figure** — see the dedicated "Known Design Tradeoff: Prediction Horizon vs. the Problem Statement's '10 Minutes Before'" section below for the full clarification; conflating the two would be statistically indefensible and is deliberately avoided everywhere in this phase's code, comments, and this log. Candidate for Sprint-0 validation (§35) recalibration of the window/horizon lengths themselves (not of the underlying clarification, which is a structural point, not a tunable). |
| `DENSITY_HEATMAP_REFERENCE_COUNT` — `backend/app/core/config.py` | 12 | `0.2` (people/cell, DensityField.grid value that saturates the heatmap color scale) | Informed by REAL observed values: a fresh Phase 9 preview re-run against `people_clip.mp4` (150 frames), restricted to frames where KDE ran at FULL confidence (no too-few-points/singular-covariance degradation — n=35 such frames), found `max_density` ranged 0.078-0.177 (median 0.127, p90=0.163) — never exceeding ~0.18 under genuine multi-point KDE smoothing on this sparse video. 0.2 sits just above that real observed ceiling. | Not calibrated against any real venue's physical capacity per cell. Deliberately does NOT use the full-run max (which often hit exactly 1.0 via the histogram-fallback degradation path on very-few-point frames — an estimation artifact, not genuine crowding; see the Phase 9 "Degraded-path density grid" row above). Candidate for Sprint-0 validation (§35) recalibration once real annotated footage is available. |
| `RISK_ELEVATED_THRESHOLD=40.0` / `RISK_CRITICAL_THRESHOLD=65.0` — `backend/app/core/config.py` | 13 | `40.0` / `65.0` (on Phase 11's `risk_score` 0-100 scale — see Resolution 1 below, NOT the Phase 1 placeholders' old 0-1 scale) | Informed by REAL observed values: a fresh preview re-run of `scripts/preview_risk_score_projection.py` against `people_clip.mp4` (149 frame-pairs) found `risk_score` p25=4.32, median=10.08, p75=22.22, p90=39.31, p95=47.12, max=53.37, mean=15.50. `RISK_ELEVATED_THRESHOLD=40.0` sits just above the real p90; `RISK_CRITICAL_THRESHOLD=65.0` sits ABOVE the real observed max — genuine CRITICAL requires conditions worse than anything this calm, sparse video ever produced. | Not empirically validated against labeled "this was genuinely elevated/critical" ground truth — informed by real observed distribution on ONE sparse video only. Candidate for Sprint-0 (§35) recalibration. **See the environment-staleness finding below — the developer's real `.env` may still shadow these with Phase 1's 0-1-scale placeholders.** |
| `RISK_INCIDENT_THRESHOLD=85.0` — `backend/app/core/config.py` | 13 | `85.0` | Diagnostic-only (decision #4) — does NOT drive a state transition this phase (Resolution 2). Set well above `RISK_CRITICAL_THRESHOLD` since no real "genuine crush incident" ground truth exists yet to calibrate against. | Not empirically validated — no incident ground truth exists in this project at all yet. Candidate for Sprint-0 (§35) recalibration once real annotated incident footage exists. |
| `RISK_STATE_FALL_HYSTERESIS_MARGIN=10.0` — `backend/app/core/config.py` | 13 | `10.0` (same 0-100 `risk_score` scale) | Informed by REAL observed values: the same `people_clip.mp4` preview re-run found mean absolute frame-to-frame `risk_score` delta ≈9.58 across 148 consecutive frame-pairs. 10.0 sits just above that real observed noise floor, so ordinary single-frame jitter alone cannot cross both a rise and its fall threshold and cause flapping. | Not empirically validated — one video's volatility profile only. A deliberate simplification of §40's plural "hysteresis margins" wording into ONE shared margin (`fall_threshold = rise_threshold - margin` for both ELEVATED and CRITICAL) rather than 3 independently-tunable fall thresholds, to keep config surface small. Candidate for Sprint-0 (§35) recalibration. |
| `RISK_STATE_PERSISTENCE_FRAMES=30` — `backend/app/core/config.py` | 13 | `30` frames (~1s at `people_clip.mp4`'s 30fps) | Same "~1s at 30fps" reasoning already used for `BOTTLENECK_WINDOW_FRAMES` (Phase 10) — long enough that a single anomalous frame can never trigger escalation (§14's explicit requirement), short enough that genuine sustained escalation doesn't feel sluggish. Also reused, unmodified, as decision #4's `incident_threshold_crossed` diagnostic-flag persistence window (a deliberate reuse rather than a new config surface for a metadata-only flag). | Not empirically validated — no sensitivity analysis on how persistence length affects false-positive/false-negative escalation rates. Candidate for Sprint-0 (§35) recalibration. |
| `VLM_COOLDOWN=30` / `FALLBACK_ANALYSIS_INTERVAL=60` — `backend/app/core/config.py` | 1 (placeholder), genuinely activated 13 | `30` seconds / `60` seconds | Phase 1 placeholder values, never consumed by any code until this phase's `TriggerEngine` (decisions #6/#7). Kept at their original numbers — reasonable round defaults, not re-derived — but now genuinely documented and load-bearing: `VLM_COOLDOWN` rate-limits repeated RISK firing at the same severity level; `FALLBACK_ANALYSIS_INTERVAL` paces the risk-independent periodic check. | Not empirically validated — no real VLM cost/latency model exists yet to optimize either value against. Candidate for Sprint-0 (§35) recalibration once Vision Intelligence is built. |

## Implementation-Discovered Constraints

Unlike the table above (tunable defaults chosen by engineering judgment,
open to recalibration), this section documents hard behavioral constraints
discovered empirically while integrating real third-party libraries — not
values to tune, but rules a future consumer of these components must
respect.

| Constraint | Discovered In | Detail | Practical Implication |
|---|---|---|---|
| `ByteTrackAdapter` / `trackers.ByteTrackTracker` enforces strict timestamp monotonicity | Phase 8→9 bridging check — two-pass memory investigation (`scripts/preview_full_pipeline.py`) | The underlying library silently no-ops any `update()` call whose `timestamp` is earlier than one it has already seen on that instance. Confirmed empirically, not assumed: re-running the same 300-frame span through the SAME tracker instance a second time triggered exactly 300 `UserWarning: ... timestamp X is earlier than the previous timestamp ... Skipping update` warnings — one per frame — with the tracker doing essentially nothing (no real association, no state update) for the entire second pass, even though the frames themselves were valid and detection/optical-flow processed them normally. | A single `Tracker` instance must process exactly ONE continuous, monotonically-increasing pass through one video, start to finish — it must never be restarted, replayed, or fed frames out of temporal order. This is in addition to (not a replacement for) Phase 7's existing "never reuse a Tracker instance across two different videos" rule. The not-yet-built AnalysisOrchestrator (§28) must construct a fresh `ByteTrackAdapter` per video/session and must never re-feed it a session that could produce a non-increasing `timestamp_seconds` sequence — e.g. a naive retry/replay path that just calls `update()` again from frame 0 would silently produce near-empty tracking output with no hard error. |
| **[FIXED]** `PressureProjector`'s TIME-based rolling window did NOT gracefully handle a non-monotonic/reset timestamp sequence — unlike `ByteTrackAdapter`, it did not no-op, it silently over-retained | Discovered: Phase 11→12 bridging check — two-pass replay run (`scripts/preview_full_crowd_intelligence_bridging.py`). Fixed: immediate bug-fix follow-up task (`predictive_projection.py`, `PressureProjector.update()`). | ORIGINAL DEFECT: `update()`'s prune step (`cutoff = latest_timestamp - PREDICTIVE_WINDOW_SECONDS`, keep only entries with `t >= cutoff`) assumed `latest_timestamp` only ever increases. Confirmed empirically on a 600-frame two-pass replay of the same video through the SAME `PressureProjector` instance: Pass 1's `data_points_used` stayed correctly bounded (max 301, matching `PREDICTIVE_WINDOW_SECONDS=10.0s` @ 30fps). At the start of Pass 2, `latest_timestamp` dropped back to ~0s (the replayed video's own timestamps restart from 0) while the window still held Pass 1's tail entries (timestamps ~10-20s) — the resulting `cutoff` became negative, so nothing got pruned, and the window ballooned to `data_points_used=602` by the end of Pass 2. NOT a genuine memory leak in absolute terms (small `(float, float)` tuples, a few KB even at 2x size) and NOT visible under RSS measurement — a CORRECTNESS defect in the window-bound invariant, not a memory-safety one. THE FIX: `update()` now compares each new `pressure.timestamp_seconds` against the window's current latest entry BEFORE appending anything; if the new timestamp is `<=` the latest one already held, the update is REJECTED — logged via `logger.warning` (this module's own `logging.getLogger(__name__)`, matching the codebase's existing convention in `app/api/auth.py`, since there is no `warnings.warn` convention of this project's own to mirror — the ByteTrackAdapter warning the task referenced comes from the third-party `trackers` library, not from this codebase), the existing window is left completely untouched, and the rejection is made OBSERVABLE via a new `PressureProjector.last_update_rejected: bool` attribute (never just logged-and-forgotten, per this project's "never fail silently" principle) — `update()` also returns `None` for a rejected call, same as its existing "not enough data yet" case, but the two are now distinguishable via `last_update_rejected`. RE-VERIFIED after the fix: re-running the same 600-frame two-pass bridging script now shows Pass 2 emitting exactly 599 rejection warnings (one per frame, since every Pass-2 timestamp is `<=` Pass 1's final timestamp under full-video replay) and `projection_available_count=0` for Pass 2 — `data_points_used` never exceeds Pass 1's correctly-bounded max of 301 anywhere in the run. | Now mirrors `ByteTrackAdapter`'s FAILURE MODE, not just its underlying constraint: a `PressureProjector` instance, like a `Tracker` instance, must be fed exactly ONE continuous, monotonically-increasing pass through one video — but a violation is now safely rejected (loud warning + observable flag + untouched state) rather than silently corrupting the window's time-bound invariant. The not-yet-built AnalysisOrchestrator (§28) must still apply the "exactly one fresh instance per video/session, never replayed" discipline (this fix is a safety net against MISUSE, not a license to replay), but a misuse bug in that future orchestration code would now be caught via `last_update_rejected` and the warning log instead of manifesting as silent over-retention. Regression-tested in `test_predictive_projection.py` (`test_non_monotonic_timestamp_is_rejected_not_silently_absorbed`, `test_monotonic_operation_completely_unaffected_by_rejection_guard`). |
| Stale `.env` placeholder thresholds silently shadow Phase 13's new calibrated `RISK_ELEVATED_THRESHOLD`/`RISK_CRITICAL_THRESHOLD`/`RISK_INCIDENT_THRESHOLD` defaults, and interact badly with the new `RISK_STATE_FALL_HYSTERESIS_MARGIN` | Discovered running `scripts/preview_risk_trigger.py` against `people_clip.mp4` (Phase 13) | These three keys are PRE-EXISTING (Phase 1 placeholders, 0-1 scale: `0.5`/`0.75`/`0.9`) and the developer's real `.env` already sets them — pydantic-settings' env-file source takes precedence over `config.py`'s class defaults, so this phase's new, real, 0-100-scale-calibrated defaults (`40.0`/`65.0`/`85.0`) are silently shadowed in this environment (this project never edits the developer's real `.env`). Confirmed empirically: running the preview script under the live (stale) config, `people_clip.mp4`'s real `risk_score` values (typically 10-50 on the 0-100 scale) trivially exceed `0.5`/`0.75`, so the state machine escalated all the way to CRITICAL by frame 63 of 150 — NOT the expected calm/NORMAL behavior for this established sparse video. Worse, this ALSO silently broke de-escalation: `RISK_STATE_FALL_HYSTERESIS_MARGIN` (a genuinely NEW key, correctly defaulting to `10.0`, NOT present in the stale `.env`) combined with the stale `RISK_CRITICAL_THRESHOLD=0.75` produces `fall_critical = 0.75 - 10.0 = -9.25` — permanently negative, so NO non-negative `risk_score` (the type is clipped to `[0, 100]` by `compute_risk_score`) can ever satisfy `risk_score < fall_critical`, making de-escalation from CRITICAL mathematically unreachable under this specific stale-vs-fresh key mismatch. Re-running the SAME script with the three threshold keys overridden via one-off process environment variables (NOT the real `.env` — env vars take precedence over `.env` in pydantic-settings' source order, so this is a non-invasive way to demonstrate intended behavior) reproduced the CORRECT, expected result: 0 real-video transitions (state stayed NORMAL the whole run, matching Phase 9-11's own repeated "sparse/low-risk video" finding) and a clean synthetic NORMAL→ELEVATED→CRITICAL escalation with 2 RISK triggers (including the cooldown-override case) in the addendum. | The code is correct and behaves exactly as designed in both runs — this is a PURE environment-configuration issue, not a code defect. `scripts/preview_risk_trigger.py` now prints the ACTIVE threshold values plus a loud warning whenever they differ from `config.py`'s own authored defaults, specifically so this class of staleness is never mistaken for a logic bug. `backend/tests/conftest.py`'s `_risk_thresholds_from_code_defaults` autouse fixture shields the test suite from this same issue by forcing `config.py`'s class defaults for these seven keys regardless of local `.env` content. **Action needed from the developer** (out of scope for this session — real `.env` is never edited here): update the real `.env`'s `RISK_ELEVATED_THRESHOLD`/`RISK_CRITICAL_THRESHOLD`/`RISK_INCIDENT_THRESHOLD` lines to match `.env.example`'s new `40.0`/`65.0`/`85.0` before relying on this phase's escalation behavior outside of tests/explicit env-var overrides. |

## Known Structural Limitation: Pixel-Space vs. Real-World Units

**Phase 9.** The Crowd Pressure formula's literature-derived thresholds
(crowd turbulence ~0.02 s^-2, stampede risk ~0.04 s^-2, per §6/§12) were
calibrated in REAL-WORLD physical units — density in people/m^2, velocity
in m/s. This project has NO camera calibration or homography step
anywhere in scope to convert pixel measurements into real-world units.

Consequently, **every density/velocity/pressure value computed by
`density.py`, `flow_field.py`, and `crowd_pressure.py` is in PIXEL-based
units** (density in people per grid cell, velocity in pixels/second,
pressure in people * pixels^2/second^2 per cell) — **NOT** the
literature's meter-based units. Applying the 0.02/0.04 threshold VALUES
literally to this project's pixel-space Pressure output would be
meaningless without a units conversion this project does not have.

This is a genuine, structural MVP limitation, not a minor detail. It is
NOT solved in Phase 9 — no crude single-scalar pixel-to-meter hack was
introduced. Pressure is computed honestly in pixel-space, and every
`CrowdPressureField` carries a `units_disclaimer` string alongside the
data itself (not just this documentation) so the limitation travels with
the data and cannot be silently missed by a downstream consumer who only
reads the numbers.

**Resolution paths for a future phase or the Sprint-0/pilot calibration
effort (§39):** either (a) add a real camera calibration/homography step
to convert pixel measurements to real-world units, allowing the
literature's thresholds to be used as-is; or (b) derive new,
pixel-space-native thresholds directly from real pilot footage, bypassing
the need for calibration entirely. Neither is implemented yet — this
section exists so the gap is discoverable by anyone reading the project's
decision history later, not just buried in code comments.

**Phase 10 addendum.** This limitation extends to Congestion's two new
thresholds (`DENSITY_CONGESTION_THRESHOLD`, `FLOW_MAGNITUDE_CONGESTION_
THRESHOLD_PX_PER_SEC`) for the same reason — both are pixel-space-native
and uncalibrated against any real venue. It does NOT extend to Reverse
Flow's four new thresholds (EMA alpha, baseline observation count,
deviation-degrees, persistence window/count) — none of those are
pixel-space quantities, so this limitation is explicitly flagged as NOT
applying to them (see the Reverse Flow row in the table above). Bottleneck's
`bottleneck_score_grid` is also exempt: it's a ratio of two same-unit
lengths (final spread / initial spread), so it's dimensionless and
self-normalizing by construction — no pixel calibration constant needed
for the score itself, only for `BOTTLENECK_WINDOW_FRAMES` (a frame count,
not a units-disclosure concern either). Only `CROWD_GRID_CELL_SIZE_PX`
(Phase 9) and Congestion's two thresholds (Phase 10) actually carry this
specific pixel-vs-meters limitation.

## Known Design Tradeoff: Density Excludes Lost Tracks

**Phase 9** (`density.py`'s `compute_density_field`). Density is computed
only from tracks with `is_lost=False` in the input `TrackingResult`. A
lost track's position is its last REAL match (Phase 7's design — never an
extrapolation), which is known-stale by the time a track goes lost;
including it would inject a potentially-outdated position into a snapshot
meant to represent "where people are right now."

This has an honest side effect worth flagging on its own: because
`ByteTrackAdapter` has its own confirmation lag (a brand-new track isn't
assigned a real `track_id` until it's matched for
`TRACKER_MINIMUM_CONSECUTIVE_FRAMES` consecutive frames — empirically
discovered in Phase 7), a just-appeared, not-yet-confirmed person is
briefly absent from `TrackingResult.tracks` entirely, and therefore briefly
absent from density too. This is a real, honest tradeoff of building
Density on top of `TrackingResult` rather than raw `DetectionResult` — not
something this phase silently works around. A future phase could
reconsider this if density needs to be more responsive to newly-arriving
people at the cost of occasionally being fed a not-yet-fully-confirmed
position.

## Known Design Tradeoff: Bottleneck's Simplified Forward-Euler Lagrangian Method (Not FTLE)

**Phase 10** (`bottleneck.py`). Bottleneck deliberately uses a Lagrangian
(tracer-advection) approach instead of the Eulerian divergence already
computed in Phase 9's `flow_field.py`, because divergence is instantaneous
and single-frame — it cannot detect people accumulating somewhere OVER
TIME the way literally following virtual tracers through several frames
of the velocity field can. This is a genuine, spec-required capability gap
Eulerian divergence cannot fill, not a redundant reimplementation.

However, this implementation is an intentional MVP SIMPLIFICATION, not the
academic finite-time Lyapunov exponent (FTLE) method a rigorous Lagrangian
coherent structures analysis would use:
- **Forward-Euler integration only** (`position += velocity * dt`) — no
  higher-order integrator (RK4, etc.). Forward-Euler accumulates more
  numerical error per step than a higher-order method would, especially
  compounded across `BOTTLENECK_WINDOW_FRAMES` steps on the coarse per-cell
  velocity field. Adequate for a directional convergence SIGNAL, not a
  precise trajectory.
- **Tracers are re-seeded at cell centers every rolling window**, not
  carried forward indefinitely — bounds memory/compute and avoids tracers
  drifting arbitrarily far from their origin cell over a long video, at
  the cost of "forgetting" convergence history older than the window.
- **"Spread"** is a simple mean-distance-from-centroid statistic over a
  cell's own tracer plus its immediate 8-connected neighbors' tracers —
  not a rigorously derived deformation-gradient eigenvalue (the actual
  FTLE quantity).
- Bilinear interpolation query positions are CLAMPED into the grid's cell-
  center coordinate range rather than extrapolated beyond it — a tracer
  that would have advected outside the frame is instead held at the
  nearest edge for interpolation purposes each step, which slightly
  understates convergence/divergence strength very near frame edges.

The resulting `bottleneck_score_grid` ratio (final spread / initial
spread) IS genuinely dimensionless and self-normalizing (a design
advantage explicitly noted in the Phase 10 prompt) — this simplification
is about the trajectory-computation method, not the resulting score's
interpretability. Not validated against any labeled "this location was a
real bottleneck" ground truth. Candidate for a future phase to upgrade to
a higher-order integrator or true FTLE if the simplified signal proves
too noisy or too weak in practice.

## Known Design Tradeoff: Reverse Flow Mechanism Not Validated for Crowds

**Phase 10** (`reverse_flow.py`). This mechanism (per-zone learned EMA
direction baseline + cosine-based angular deviation + rolling-window
temporal persistence) is explicitly adapted from vehicle wrong-way-
detection literature. The master spec itself flags this adaptation as
needing pedestrian-domain validation — pedestrians do not move like
vehicles: they have far more locally-variable, non-lane-constrained
motion, so a "wrong way" concept borrowed from traffic monitoring may
turn out to be a poor behavioral model for crowds (e.g. flagging normal
counter-flow at a doorway or a person weaving through a queue as "reverse
flow" when it isn't a meaningful safety signal). This implementation
builds the MECHANISM honestly and exactly as specified — it does not
claim, here or anywhere else in this codebase, that the mechanism has
been validated as behaviorally correct for pedestrian crowds.

On the one real video available in this project (`people_clip.mp4`, a
sparse, low-density scene), reverse flow triggered only briefly and
marginally (frames 95-101 of a 149-frame-pair run; 1-3 of 252 cells
flagged at a time; `reverse_flow_cell_fraction` never exceeding 0.012) —
too weak and too brief a signal on this one video to serve as either
validation or invalidation of the mechanism's real-world usefulness for
crowds. Candidate for real pedestrian-domain validation once labeled
"genuine wrong-way flow" footage is available (Sprint-0/pilot, §35/§39).

## Known Design Choice: Risk Score's Combination Scheme Is Engineering Judgment, Not Spec-Derived

**Phase 11** (`risk_score.py`). The master spec gives an exact formula for
Crowd Pressure (§12) but does NOT specify how to combine Pressure with
Congestion/Bottleneck/Reverse Flow (Phase 10) into a single 0-100 Risk
Score. The whole combination scheme — per-signal 0-100 sub-scores,
weighted-average combination, Pressure weighted largest, proportional
weight redistribution on missing data, confidence propagated (not
invented) from `DensityField.estimation_confidence` — is a documented
engineering judgment call, not something derived from the master spec's
own formulas. Every numeric input to this scheme (`PRESSURE_SCORE_
REFERENCE_PX`, the four `RISK_SCORE_WEIGHT_*` values) is logged
individually in the table above; this entry exists to flag the SCHEME
itself (the choice to do a weighted average of independently-normalized
sub-scores at all, rather than e.g. a max, a product, or a learned model)
as also unvalidated, separately from its individual constants.

**Confidence vs. completeness, applied one layer earlier than the spec
describes it.** `RiskScoreResult.contributing_signals` records which of
the four sub-scores actually fed into a given frame's score. This is the
same distinction the master spec draws explicitly at §16's Evidence
Package (confidence = "how much do we trust the numbers we have" vs.
completeness = "how much of the full picture do we have at all") — applied
here one pipeline layer earlier than the spec explicitly describes it,
at the level of an individual frame's risk score rather than a full
Evidence Package. `confidence` and `contributing_signals` are deliberately
two separate fields for exactly this reason: a frame can have full
confidence (clean density estimation) while missing a signal
(Bottleneck's window still filling), or vice versa.

## Known Design Choice: Pressure Sub-Score Uses max_pressure, Projection Uses mean_pressure

**Phase 11** (`risk_score.py` vs. `predictive_projection.py`). These two
modules deliberately use DIFFERENT Crowd Pressure summary statistics from
the same underlying `CrowdPressureField` — this is intentional, not an
inconsistency:
- `risk_score.py`'s pressure sub-score uses **`max_pressure`**: a single
  frame's risk snapshot should be worst-case-sensitive, since one small
  area beginning to crush matters even if every other area is calm (this
  matches the problem statement's own framing of crush risk as a
  localized, not average, phenomenon).
- `predictive_projection.py`'s trend fit uses **`mean_pressure`**: a
  per-cell MAXIMUM is likely to be noisier/jumpier frame-to-frame (the
  identity of "the worst cell" can shift from one frame to the next),
  which would make a linear trend fit unstable. `mean_pressure`'s
  frame-to-frame trajectory is smoother and better suited to a lightweight
  linear-regression extrapolation.

Both choices are documented explicitly in each module's own docstring so
neither looks like an accidental oversight of the other.

## Known Design Tradeoff: Prediction Horizon vs. the Problem Statement's "10 Minutes Before"

**Phase 11** (`predictive_projection.py`). The problem statement's
aspiration is to answer "is there a risk of crowd crush within the next
few minutes" — commonly stated elsewhere in this project's source
material as roughly "10 minutes before." `PREDICTION_HORIZON_SECONDS`
defaults to 30 seconds, extrapolated from a `PREDICTIVE_WINDOW_SECONDS`-
wide (default 10 seconds) rolling window of recent mean_pressure data.
These numbers are DELIBERATELY NOT an attempt at the "10 minutes"
figure — confidently linear-extrapolating 10 minutes forward from a
10-second window of noisy real-world pressure data would be statistically
indefensible, and neither the code nor its docstrings nor this log make
that claim anywhere.

The spec's "10 minutes before" is understood in this project as a
SYSTEM-LEVEL goal, achieved through SUSTAINED trend detection and
risk-state persistence over time — watching this lightweight projection
(and the Risk Score, and other signals) evolve across MANY frames and
MANY windows, which is explicitly the not-yet-built Risk State + Trigger
Engine's job, not a literal guarantee this single-window, single-call
linear fit makes on its own. This distinction is logged here specifically
so a future reader does not mistake `PREDICTION_HORIZON_SECONDS=30` for a
failed or scaled-down attempt at "10 minutes" — it was never attempting
that in the first place.

## Milestone: CrowdMetrics Now Fulfills the Full §28 Contract

**Phase 11** (`crowd_metrics.py`). Phases 9 and 10 both explicitly
documented `CrowdMetrics` as a deliberate SUBSET of the full §28
`CrowdIntelligence.analyze(tracking, motion, temporal_state) ->
CrowdMetrics` contract, each time naming what was still missing
(`risk_score`, a propagated `confidence`). As of this phase, both exist
(`RiskScoreResult.risk_score`/`.confidence` on the bundle, plus
`predictive_projection`) — `CrowdMetrics` now fulfills the full §28 data
contract. What remains explicitly OUT of scope is not part of that data
contract at all: the Risk State + Trigger Engine that CONSUMES this
contract to classify NORMAL/ELEVATED/CRITICAL/INCIDENT with hysteresis,
and the mandatory 5-heatmap-type VISUALIZATION system — both later,
separate phases that read this data, neither one an addition to the
`CrowdMetrics` dataclass itself.

## Known Design Choice: Risk Heatmap Is a Per-Cell Reapplication of Phase 11's Formula, Not the Same Number Spatialized

**Phase 12** (`heatmap_rendering.py`'s `render_risk_heatmap`, Resolution
1). Phase 11's `risk_score.py` computes ONE scalar per frame using
per-signal REDUCTIONS: `max_pressure` (a single worst-cell number),
`congested_cell_fraction` (a single grid-wide fraction), the grid's
minimum bottleneck ratio, and `reverse_flow_cell_fraction` (another
single grid-wide fraction). That's correct for its purpose — the future
Trigger Engine needs one thresholdable number per frame, not a grid.

This phase needed a genuinely SPATIAL risk artifact, since a heatmap by
definition renders a grid. Rather than inventing a new, undocumented
scoring scheme, `render_risk_heatmap` applies Phase 11's EXACT SAME
weighted-combination formula and EXACT SAME configured weights
(`RISK_SCORE_WEIGHT_*`) POINTWISE — even importing `risk_score.py`'s own
`_redistribute_weights` helper directly rather than reimplementing it
(verified by identity, not just value equality, in
`test_heatmap_rendering.py`). Each signal's own NATIVE per-cell field is
used instead of Phase 11's frame-level reduction:
- pressure: `CrowdPressureField.grid[cell] / PRESSURE_SCORE_REFERENCE_PX * 100`
- congestion: `CongestionField.congestion_score_grid[cell] * 100`
- bottleneck: `(1 - BottleneckField.bottleneck_score_grid[cell]) * 100`
- reverse_flow: `ReverseFlowField.is_reverse_flow_grid[cell] ? 100 : 0`

**This is philosophically consistent with, but NOT numerically identical
to, Phase 11's scalar `risk_score`** — different reduction method per
signal (max/min/fraction reductions vs. direct pointwise combination), so
the two will generally differ even on the same frame. `test_heatmap_
rendering.py`'s correlation test checks only a REASONABLE DIRECTIONAL
relationship (a low-risk scenario produces both a lower scalar AND a
lower heatmap mean/max than a high-risk scenario) — exact equality is
never asserted and is not the expected bar. Bottleneck-unavailable
handling mirrors Phase 11 exactly (weight redistribution across the
remaining signals), extended one level more granularly to individual
NaN cells (only possible on a degenerate 1-row/1-col grid).

## Known Design Choice: Predictive Heatmap Is a Trend-Scaled View, Not an Independent Per-Cell Forecast

**Phase 12** (`heatmap_rendering.py`'s `render_predictive_heatmap`,
Resolution 2). Master spec §12 states heatmaps render "already-computed
fields" — genuine per-cell time-series forecasting (fitting an
independent trend PER GRID CELL) would be COMPUTING NEW DATA, which is
explicitly outside a rendering phase's charter. This phase therefore does
NOT build per-cell forecasting. Instead, the CURRENT
`CrowdPressureField.grid` (the real, already-computed spatial PATTERN) is
uniformly SCALED by the ratio `projection.projected_pressure / current
mean_pressure` — the trend applies a single scalar adjustment to the
existing spatial shape rather than inventing a new one. If current
`mean_pressure` is exactly 0.0 (an empty/still scene with no spatial
pattern to scale), scaling is skipped entirely and the projected value is
used directly as a uniform low-level field instead of dividing by zero —
handled and tested explicitly (`test_predictive_heatmap_zero_current_
mean_pressure_does_not_divide_by_zero`).

This limitation — "trend-scaled view of the current pattern, NOT an
independent forecast" — is embedded as VISIBLE TEXT directly on the
rendered image itself (decision #6), not only documented in code or this
log, and its presence is verified by a concrete pixel-region test
(`test_predictive_heatmap_trend_disclaimer_genuinely_present`), not just
trusted from reading the render function's source. This is the first
phase where the rendered artifact IS the deliverable a downstream
consumer (dashboard, API client) actually sees — Phase 9's equivalent
units disclaimer only ever had to travel as far as console output and a
dataclass field.

Per Resolution 2, when `CrowdMetrics.predictive_projection` is `None`
(PressureProjector's window hasn't accumulated enough history yet, per
Phase 11), the Predictive heatmap is SKIPPED for that generation event
entirely — never fabricated from a missing projection. This is the ONLY
one of the 5 mandatory types that can ever be skipped (decision #7);
Density/Pressure/Flow-Congestion/Risk are always generated, with Risk's
own Bottleneck-unavailable case absorbed internally via weight
redistribution rather than a skip.

## Known Design Choice: Two New API Routes Are a Deliberate, Reasoned Exception

**Phase 12** (`app/api/heatmaps.py`, Resolution 3). Every phase since
Phase 6 (Detection) through Phase 11 (Risk Score/Predictive Projection)
deliberately added NO new API routes — those phases were pure in-memory
pipeline primitives with nothing persisted worth exposing yet. This
phase breaks that pattern, on purpose, because master spec §26
explicitly names `GET /sessions/{id}/heatmaps` and `GET /sessions/{id}
/heatmaps/{type}` as FROZEN API surface, and — unlike a hypothetical
premature "detection results" route in an earlier phase would have
been — `heatmap_snapshots` is by THIS PHASE'S OWN DESIGN a genuinely
PERSISTED, session-scoped DB resource: a real foreign key to
`analysis_sessions`, real JPEG files on disk, real rows a client could
legitimately want to list or fetch metadata for right now. Adding the
routes here is exposing something that already durably exists, not
building ahead of the data model the way an early route would have.

Both routes are DELIBERATELY READ-ONLY and METADATA-ONLY:
- Neither serves raw image bytes — `HeatmapSnapshotRead` excludes
  `file_path` entirely (same reasoning `VideoRead` excluded
  `storage_filename` in Phase 3), and actual byte serving is deferred to
  the dashboard integration phase, for UNIFORM treatment of all static
  asset serving handled together later (video streaming was deferred for
  the identical reason in Phase 3 — this isn't a new inconsistency, it's
  the same precedent applied again).
- Neither triggers generation on demand — both only query
  ALREADY-PERSISTED `HeatmapSnapshot` rows created by
  `generate_and_persist_heatmaps` (called by `scripts/preview_heatmaps.py`
  for this phase's own verification, and by a future orchestration phase
  in production — never by these routes themselves).

Verified, not just asserted: `test_heatmaps_api.py` covers 401 (no auth),
200 with correct envelope shape and `file_path` genuinely absent from
every response, 400 for an invalid `{type}` path segment, and 404 for
both a nonexistent session and a valid type with no snapshot yet.

## Resolution 1 (Phase 13): RiskStateMachine Classifies risk_score, Not Raw Crowd Pressure

**Phase 13** (`risk_state.py`), continuing the units-caveat thread started
at Phase 9's Critical Units Disclosure and carried through Phases 10-12.
Master spec §14 frames risk states as "operationalizing the underlying
Crowd Pressure thresholds" and cites literature real-world SI values
(~0.02/0.04 s⁻²). As established since Phase 9, this project's computed
Crowd Pressure remains PIXEL-space (no camera calibration exists), so
those literal literature numbers are inapplicable here — using them
directly would silently misrepresent a pixel-space quantity as a
calibrated physical one.

`RiskStateMachine` sidesteps this the same way every phase since Phase 9
has: it classifies state from Phase 11's `risk_score`
(`RiskScoreResult.risk_score`), NOT from a raw `CrowdPressureField` value.
`risk_score` is already 0-100 normalized/dimensionless (Phase 11's own
`compute_risk_score` clips it to that range) and already weighted 50%
toward Pressure by Phase 11's own design
(`RISK_SCORE_WEIGHT_PRESSURE=0.5`) — so Pressure's real-world significance
is still the dominant input to the classification, just laundered through
Phase 11's normalization rather than consumed as a raw pixel-space number
with an inapplicable literature threshold bolted onto it. This is an
honest sidestep of the units mismatch, not a resolution of it — the
underlying "no camera calibration" limitation (see "Known Structural
Limitation: Pixel-Space vs. Real-World Units" above) is unchanged by this
phase.

## Resolution 2 (Phase 13): INCIDENT Is Defined but Structurally Unreachable — and Is NOT the Future Incident Entity

**Phase 13** (`risk_state.py`). Per §14's own state diagram, the
CRITICAL → INCIDENT transition is gated by "Decision Intelligence
confirms" — a component that does not exist until a much later roadmap
phase. `RiskState` defines the full eventual four-value enum
(`NORMAL`/`ELEVATED`/`CRITICAL`/`INCIDENT`) NOW, in this phase, the same
honest pattern Phase 4's `SessionStatus` used when it shipped
`PROCESSING`/`COMPLETED`/`FAILED` values no code path could produce yet
— but `RiskStateMachine` has NO code path, anywhere, that can ever
produce `INCIDENT`. Its real ceiling this phase is CRITICAL, structurally
(see `_evaluate_candidate`'s `RiskState.CRITICAL` branch — there is no
upward candidate defined there at all, not merely an unmet threshold
check). Proven, not just asserted, by
`test_risk_state.py::test_incident_state_is_structurally_unreachable_this_phase`,
which feeds `risk_score=100.0` (the maximum possible value) for 400
consecutive frames and confirms the observed-states set never contains
`RiskState.INCIDENT` and the final state is `RiskState.CRITICAL`.

**CRITICAL NAMING DISAMBIGUATION** (worth over-stating given its
importance for later phases): `RiskState.INCIDENT` is a classification
LABEL produced by THIS state machine only. It is NOT the same thing as
the future "Incident" DATABASE ENTITY (§19, roadmap Phase 18) with its
own `DETECTED`/`ACTIVE`/`RESOLVED` lifecycle and operator actions
(acknowledge/dismiss/resolve/escalate, §20). Phase 13 creates NO table,
model, or route named plain "incidents" and implements NO operator
incident-management actions — `risk_events` (this phase's ONLY new
table) records risk-STATE TRANSITIONS, not incident lifecycle events.
Decision #3's related point: de-escalation FROM `INCIDENT` specifically
("operator resolves") is explicitly out of scope this phase — it would
require the real Incident Manager architecture that doesn't exist until
Phase 18. Since `INCIDENT` is unreachable this phase anyway (per above),
this is currently a moot/theoretical exclusion, documented here for
completeness rather than because it changes any observable behavior
today.

## Known Design Choice: Trigger Priority and the RISK-Cooldown Override

**Phase 13** (`trigger_engine.py`, decisions #5/#6/#9). `TriggerEngine`
checks conditions in a fixed, documented priority order every
`evaluate()` call: **OPERATOR > RISK > FALLBACK > NONE**. An explicit
human request always wins (decision #8's plumbing-only OPERATOR flag);
a genuine risk escalation is checked next; a routine periodic FALLBACK
check is lowest priority. When RISK and FALLBACK are both eligible in
the same call, RISK wins AND FALLBACK's own interval timer is left
untouched (not consumed) — so it remains eligible on the very next
call, rather than being silently reset by a cycle it didn't actually
get to fire in
(`test_trigger_engine.py::test_priority_risk_wins_over_fallback_when_both_true_and_fallback_stays_pending`
verifies this exact mechanic).

RISK reuses `RiskStateResult.state_changed_this_frame` plus a severity-
rank comparison against the state seen on the PREVIOUS `evaluate()` call
(decision #5) — rather than a second, independently-tunable threshold
that could disagree with `RiskStateMachine` right at a boundary. Once
RISK fires, further RISK firing at the SAME severity level is suppressed
for `VLM_COOLDOWN` seconds — EXCEPT a genuinely HIGHER escalation during
that cooldown window (e.g. ELEVATED→CRITICAL shortly after an earlier
NORMAL→ELEVATED firing) overrides the cooldown and fires immediately
(decision #6) — a real crisis worsening must never be silently swallowed
by a cooldown whose only purpose is preventing redundant re-triggering at
an unchanged severity. Verified end-to-end, not just in isolated unit
tests, by `scripts/preview_risk_trigger.py`'s synthetic addendum: feeding
a value between the ELEVATED and CRITICAL rise thresholds first (fires
RISK, starts cooldown), then a value above the CRITICAL rise threshold
well inside the cooldown window, produced exactly the override firing
with `reason` containing "(cooldown override: new higher escalation)".
FALLBACK and OPERATOR are never subject to this cooldown at all (decision
#6's own scope) — FALLBACK has its own independent `FALLBACK_ANALYSIS_
INTERVAL` timer, and OPERATOR is a deliberate human action that must
never be silently suppressed.
