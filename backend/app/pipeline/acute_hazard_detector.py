"""Acute Hazard Detector — new architectural decision (see DECISIONS.md,
"Acute-Hazard Trigger Phase"). A deterministic, multi-signal scene-anomaly
detector that answers a narrower question than Risk State: not "is crowd
pressure high," but "did something change abruptly enough that semantic
visual inspection is warranted right now, even though crowd-crush risk may
not be." Feeds `TriggerEngine.evaluate()` as a NEW, independent trigger
source (`TriggerType.ACUTE_HAZARD`) — this module makes no VLM/LLM call and
declares no incident itself; it only produces a deterministic signal.

============================================================
WHY THIS EXISTS (root cause, see DECISIONS.md for the full writeup)
============================================================
`TriggerEngine`'s only two automatic trigger sources are RISK (crowd-crush
`risk_score` escalation) and FALLBACK (a fixed real-time interval,
`FALLBACK_ANALYSIS_INTERVAL=60s`). A short, otherwise-calm video containing
a sudden severe event (e.g. an explosion) that does not itself move Crowd
Pressure/Congestion/Bottleneck/Reverse-Flow enough to cross
`RISK_ELEVATED_THRESHOLD`, and that is shorter than `FALLBACK_ANALYSIS_
INTERVAL`, can complete its ENTIRE run without ever invoking the VLM at
all — not because the model failed to interpret the scene, but because it
was never shown the scene. This detector closes that specific gap using
only already-computed-or-cheap signals, never the VLM itself.

============================================================
FOUR CANDIDATE SIGNALS (all already computed elsewhere in Loop A, or cheap)
============================================================
A. `motion_energy` — MotionResult.mean_velocity (global aggregate motion
   magnitude, already computed every frame by DISOpticalFlowAdapter, BEFORE
   CrowdMetricsEngine even runs).
B. `flow_divergence` — max(|FlowGridField.grid_divergence|) across cells
   (already computed every frame via CoreCrowdMetrics.flow). Divergence
   measures SPATIAL VARIATION of motion, not raw magnitude — a uniform
   camera pan/jitter spikes A but leaves B near zero (near-uniform velocity
   has near-zero spatial gradient). This is the camera-motion mitigation
   the design explicitly requires — see `_QUORUM` discussion below.
C. `detection_count_delta` — |current frame's detection count - previous
   frame's| (already computed by the detector stage; an explosion can
   obscure people or scatter them, producing a real discontinuity here).
D. `scene_change` — NEW, cheap: normalized mean-absolute grayscale
   difference between consecutive frames (`compute_scene_change_score`
   below). Dominated in cost by the already-heavier YOLO/DIS calls in the
   same loop iteration.

============================================================
FUSION — QUORUM OF CORROBORATING SIGNALS, NOT PERSISTENCE (decision E)
============================================================
Unlike `RiskStateMachine`'s N-consecutive-frames hysteresis (appropriate
for a GRADUALLY building crowd-crush condition), an explosion's most
extreme signature may last only 1-3 frames before settling into ordinary
post-event dispersal motion — requiring sustained persistence would filter
out the exact event this detector exists to catch. Instead: each signal
maintains its own online EMA baseline (mean, variance) — same mechanism as
`ReverseFlowDetector`'s per-cell direction EMA, applied here to four
frame-level scalars. A signal "fires" when its z-score-like deviation from
that baseline exceeds `ACUTE_HAZARD_ZSCORE_THRESHOLD`. The detector as a
whole only flags `is_acute_hazard=True` when BOTH: (1) at least
`ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS` (default 2 of 4) fire on the SAME
frame, AND (2) at least one of the firing signals is "spatially
discriminating" (flow_divergence or detection_count_delta — see
`_SPATIALLY_DISCRIMINATING_SIGNALS`). Condition (2) is the actual camera-
motion/global-jitter mitigation: A (motion_energy) and D (scene_change) are
both GLOBAL, frame-wide signals, so a genuine camera pan/shake can spike
them TOGETHER and would otherwise satisfy a plain "2 of 4" quorum on its
own — REAL BUG CAUGHT WHILE TESTING THIS (see DECISIONS.md): the first
version of this module had exactly that gap, caught by
`test_acute_hazard_detector.py`'s synthetic uniform-global-motion case
actually flagging `is_acute_hazard=True` before this fix. B (divergence)
and C (detection count) are the two signals that actually distinguish
"something localized happened" from "the whole frame moved/changed" —
requiring one of them specifically closes the gap.

BASELINE CONTAMINATION GUARD: a signal's EMA baseline is updated with the
current frame's value ONLY when that specific signal did NOT itself fire
this frame — an anomalous frame's own extreme value never gets folded into
"what normal looks like," which would otherwise let the baseline drift
toward the abnormal state within a few frames and mask a sustained event.

WARM-UP: `ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS` frames must be observed
per signal before it is ever eligible to fire — mirrors `ReverseFlowDetector`'s
`REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS` pattern exactly, preventing the
first few frames from trivially "spiking" against an empty baseline.

============================================================
THRESHOLD CALIBRATION STATUS (see DECISIONS.md)
============================================================
The real regression video the developer described (an explosive-blast
clip) was found, on inspection, to be byte-for-byte identical to
`people_clip.mp4` (same SHA256) — i.e. the actual blast footage has not
yet been supplied to this repo. Per the developer's own explicit choice,
this module was built and unit-tested against SYNTHETIC fixtures now,
with the defaults below marked UNVALIDATED ENGINEERING JUDGMENT pending a
real calibration run (`scripts/preview_acute_hazard_signals.py`, written
but not yet run against real blast footage) once that footage is
supplied. Defaults below follow this project's own established
"~1s at 30fps" warm-up reasoning (`REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS`,
`RISK_STATE_PERSISTENCE_FRAMES`) and a standard statistical
"significant deviation" starting point (z >= 3.0) — NOT measured against
real footage yet.
"""

from collections import deque
from dataclasses import dataclass, field

import cv2
import numpy as np

from app.core.config import settings
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.detection import DetectionResult
from app.pipeline.flow_field import FlowGridField
from app.pipeline.frame import Frame
from app.pipeline.motion import MotionResult

# Canonical signal order — stable, readable regardless of dict insertion
# order, same discipline as risk_score.py's `_SIGNAL_ORDER`.
SIGNAL_ORDER = ("motion_energy", "flow_divergence", "detection_count_delta", "scene_change")

# REAL BUG CAUGHT WHILE TESTING THIS (see DECISIONS.md): a plain "2 of 4
# signals" quorum is NOT sufficient camera-motion mitigation on its own —
# motion_energy and scene_change are both GLOBAL, frame-wide signals, so a
# genuine camera pan/shake can spike BOTH of them together and reach quorum
# WITHOUT ever needing flow_divergence or detection_count_delta (the two
# signals that actually discriminate "something localized happened" from
# "the whole frame moved/changed"). Proven by
# test_acute_hazard_detector.py::test_uniform_camera_pan_does_not_flag_synthetic
# actually failing (is_acute_hazard=True) before this fix. The quorum
# requirement is therefore two-part: >= ACUTE_HAZARD_MIN_CORROBORATING_
# SIGNALS signals fire AND at least one of them is spatially discriminating.
_SPATIALLY_DISCRIMINATING_SIGNALS = frozenset({"flow_divergence", "detection_count_delta"})

# REAL BUG CAUGHT WHILE CALIBRATING THIS AGAINST REAL FOOTAGE (see
# DECISIONS.md): a single shared, near-zero floor (originally 1e-6) let any
# signal whose EMA variance happened to collapse toward zero (e.g.
# detection_count_delta staying exactly 0 for long calm stretches — very
# common) produce ABSURD z-scores from the most mundane deviation — a
# single detection-count change of 1 person registered as z=2816 on real
# people_clip.mp4 footage, firing ACUTE_HAZARD 4 times on genuinely calm
# footage (scripts/preview_acute_hazard_signals.py's own real run, logged
# in DECISIONS.md). Fixed with PER-SIGNAL floors, each informed by that
# same real calibration run's own measured distributions (not guessed in a
# vacuum): motion_energy/flow_divergence use 1.0 (their real p25 values
# were already several units, so 1.0 only guards genuine near-zero
# collapse); detection_count_delta uses 1.0 (a single person's worth of
# count noise — its own real distribution showed p75=1.0 already);
# scene_change uses 0.005 (roughly an order of magnitude below its real
# p25=0.018, since its whole real range is much smaller than the other
# three signals').
_MIN_STD_FLOOR: dict[str, float] = {
    "motion_energy": 1.0,
    "flow_divergence": 1.0,
    "detection_count_delta": 1.0,
    "scene_change": 0.005,
}


def compute_scene_change_score(prev_frame: Frame, curr_frame: Frame) -> float:
    """Signal D: normalized (0-1) mean-absolute grayscale difference between
    two consecutive frames. Cheap — dominated by the already-heavier YOLO/DIS
    calls made in the same Loop A iteration. Not itself spatially localized;
    localization for ROI selection uses signal B's per-cell grid instead
    (see `AcuteHazardSignal.localization_grid`)."""
    prev_gray = cv2.cvtColor(prev_frame.image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    curr_gray = cv2.cvtColor(curr_frame.image, cv2.COLOR_BGR2GRAY).astype(np.float32)
    return float(np.abs(curr_gray - prev_gray).mean() / 255.0)


@dataclass
class AcuteHazardSignal:
    frame_number: int
    timestamp_seconds: float
    is_acute_hazard: bool
    # Subset of SIGNAL_ORDER that individually fired (crossed z-score
    # threshold) this frame — empty when is_acute_hazard is False, UNLESS
    # exactly one signal fired (below quorum) — that case is preserved here
    # too, for diagnostics/evidence, even though it does not flag overall.
    corroborating_signals: list[str] = field(default_factory=list)
    # All four signals' raw z-score-like deviations this frame (even
    # non-firing ones) — never fabricated, always the real computed values,
    # so a persisted EvidencePackage can cite concrete numbers.
    raw_values: dict[str, float] = field(default_factory=dict)
    z_scores: dict[str, float] = field(default_factory=dict)
    # Per-cell |divergence| grid (shape rows x cols) — used by the caller to
    # localize ROI selection to where the anomaly actually is, rather than
    # the crowd-crush risk grid. Always present (zeros before warm-up).
    localization_grid: np.ndarray = field(default_factory=lambda: np.zeros((1, 1)))
    baseline_established: bool = False


class _SignalBaseline:
    """One signal's online EMA baseline (mean, variance) + warm-up counter.
    Mirrors ReverseFlowDetector's per-cell EMA-direction mechanism, applied
    here to a single frame-level scalar."""

    def __init__(self, min_std_floor: float) -> None:
        self._mean = 0.0
        self._var = 0.0
        self._observations = 0
        self._min_std_floor = min_std_floor

    @property
    def established(self) -> bool:
        return self._observations >= settings.ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS

    def z_score(self, value: float) -> float:
        if not self.established:
            return 0.0
        std = max(self._var**0.5, self._min_std_floor)
        return (value - self._mean) / std

    def observe(self, value: float) -> None:
        """Fold `value` into the baseline — caller decides whether to call
        this (see module docstring's "baseline contamination guard": skipped
        for a frame where this signal itself fired)."""
        alpha = settings.ACUTE_HAZARD_BASELINE_EMA_ALPHA
        if self._observations == 0:
            self._mean = value
            self._var = 0.0
        else:
            delta = value - self._mean
            self._mean += alpha * delta
            self._var = (1 - alpha) * (self._var + alpha * delta * delta)
        self._observations += 1


class AcuteHazardDetector:
    """STATEFUL — construct ONE fresh instance per video/session, update()
    incrementally as each new frame's signals arrive, and NEVER reuse across
    two different videos/sessions (same hard rule as every other stateful
    pipeline component since Phase 7's Tracker)."""

    def __init__(self, grid: CrowdGrid) -> None:
        self._grid = grid
        self._baselines = {
            name: _SignalBaseline(_MIN_STD_FLOOR[name]) for name in SIGNAL_ORDER
        }
        self._last_detection_count: int | None = None
        self._last_fire_timestamp: float | None = None

    def update(
        self,
        motion_result: MotionResult,
        flow_grid_field: FlowGridField,
        detection_result: DetectionResult,
        prev_frame: Frame,
        curr_frame: Frame,
    ) -> AcuteHazardSignal:
        detection_count = len(detection_result.detections)
        detection_delta = (
            0.0
            if self._last_detection_count is None
            else abs(detection_count - self._last_detection_count)
        )
        self._last_detection_count = detection_count

        divergence_grid = np.abs(flow_grid_field.grid_divergence)
        raw_values = {
            "motion_energy": motion_result.mean_velocity,
            "flow_divergence": float(divergence_grid.max()),
            "detection_count_delta": detection_delta,
            "scene_change": compute_scene_change_score(prev_frame, curr_frame),
        }

        z_scores: dict[str, float] = {}
        corroborating: list[str] = []
        for name in SIGNAL_ORDER:
            baseline = self._baselines[name]
            z = baseline.z_score(raw_values[name])
            z_scores[name] = z
            fired = baseline.established and z >= settings.ACUTE_HAZARD_ZSCORE_THRESHOLD
            if fired:
                corroborating.append(name)
            else:
                # Baseline contamination guard (module docstring): only
                # frames a signal did NOT itself flag as anomalous feed that
                # signal's own baseline.
                baseline.observe(raw_values[name])

        baseline_established = all(b.established for b in self._baselines.values())
        has_spatial_corroboration = any(s in _SPATIALLY_DISCRIMINATING_SIGNALS for s in corroborating)
        quorum_met = (
            len(corroborating) >= settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS
            and has_spatial_corroboration
        )

        cooldown_active = (
            self._last_fire_timestamp is not None
            and (curr_frame.timestamp_seconds - self._last_fire_timestamp)
            < settings.ACUTE_HAZARD_COOLDOWN_SECONDS
        )

        is_acute_hazard = baseline_established and quorum_met and not cooldown_active
        if is_acute_hazard:
            self._last_fire_timestamp = curr_frame.timestamp_seconds

        return AcuteHazardSignal(
            frame_number=curr_frame.frame_number,
            timestamp_seconds=curr_frame.timestamp_seconds,
            is_acute_hazard=is_acute_hazard,
            corroborating_signals=corroborating,
            raw_values=raw_values,
            z_scores=z_scores,
            localization_grid=divergence_grid,
            baseline_established=baseline_established,
        )
