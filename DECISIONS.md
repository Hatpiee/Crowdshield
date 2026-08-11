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

## Implementation-Discovered Constraints

Unlike the table above (tunable defaults chosen by engineering judgment,
open to recalibration), this section documents hard behavioral constraints
discovered empirically while integrating real third-party libraries — not
values to tune, but rules a future consumer of these components must
respect.

| Constraint | Discovered In | Detail | Practical Implication |
|---|---|---|---|
| `ByteTrackAdapter` / `trackers.ByteTrackTracker` enforces strict timestamp monotonicity | Phase 8→9 bridging check — two-pass memory investigation (`scripts/preview_full_pipeline.py`) | The underlying library silently no-ops any `update()` call whose `timestamp` is earlier than one it has already seen on that instance. Confirmed empirically, not assumed: re-running the same 300-frame span through the SAME tracker instance a second time triggered exactly 300 `UserWarning: ... timestamp X is earlier than the previous timestamp ... Skipping update` warnings — one per frame — with the tracker doing essentially nothing (no real association, no state update) for the entire second pass, even though the frames themselves were valid and detection/optical-flow processed them normally. | A single `Tracker` instance must process exactly ONE continuous, monotonically-increasing pass through one video, start to finish — it must never be restarted, replayed, or fed frames out of temporal order. This is in addition to (not a replacement for) Phase 7's existing "never reuse a Tracker instance across two different videos" rule. The not-yet-built AnalysisOrchestrator (§28) must construct a fresh `ByteTrackAdapter` per video/session and must never re-feed it a session that could produce a non-increasing `timestamp_seconds` sequence — e.g. a naive retry/replay path that just calls `update()` again from frame 0 would silently produce near-empty tracking output with no hard error. |

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
