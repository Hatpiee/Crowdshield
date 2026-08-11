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
