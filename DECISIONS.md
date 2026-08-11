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

## Implementation-Discovered Constraints

Unlike the table above (tunable defaults chosen by engineering judgment,
open to recalibration), this section documents hard behavioral constraints
discovered empirically while integrating real third-party libraries — not
values to tune, but rules a future consumer of these components must
respect.

| Constraint | Discovered In | Detail | Practical Implication |
|---|---|---|---|
| `ByteTrackAdapter` / `trackers.ByteTrackTracker` enforces strict timestamp monotonicity | Phase 8→9 bridging check — two-pass memory investigation (`scripts/preview_full_pipeline.py`) | The underlying library silently no-ops any `update()` call whose `timestamp` is earlier than one it has already seen on that instance. Confirmed empirically, not assumed: re-running the same 300-frame span through the SAME tracker instance a second time triggered exactly 300 `UserWarning: ... timestamp X is earlier than the previous timestamp ... Skipping update` warnings — one per frame — with the tracker doing essentially nothing (no real association, no state update) for the entire second pass, even though the frames themselves were valid and detection/optical-flow processed them normally. | A single `Tracker` instance must process exactly ONE continuous, monotonically-increasing pass through one video, start to finish — it must never be restarted, replayed, or fed frames out of temporal order. This is in addition to (not a replacement for) Phase 7's existing "never reuse a Tracker instance across two different videos" rule. The not-yet-built AnalysisOrchestrator (§28) must construct a fresh `ByteTrackAdapter` per video/session and must never re-feed it a session that could produce a non-increasing `timestamp_seconds` sequence — e.g. a naive retry/replay path that just calls `update()` again from frame 0 would silently produce near-empty tracking output with no hard error. |
