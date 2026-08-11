"""Predictive Projection (master spec §6: "a lightweight autoregressive
projection applied specifically to the Crowd Pressure time series") —
roadmap Phase 11's final piece. Projects Crowd Pressure ONLY — this module
must never grow a "predicted risk score" of any kind; risk_score.py's
output is not a time series this module touches.

WHY mean_pressure, NOT max_pressure (deliberately different from
risk_score.py's `_pressure_subscore`, which uses max_pressure — this is
not an inconsistency, it's a considered choice per decision #6): a
per-cell MAXIMUM is likely to be noisier/jumpier frame-to-frame, because
the identity of "the worst cell" can shift from one frame to the next
(e.g. a brief KDE artifact spikes one cell, then a neighboring cell spikes
next frame) — such a series makes a linear trend fit unstable.
mean_pressure's frame-to-frame trajectory is smoother and better suited to
a lightweight linear-regression trend than max_pressure would be.
risk_score.py's use of max_pressure is deliberately worst-case-sensitive
for a single frame's snapshot score; this module's use of mean_pressure is
deliberately trend-stable for a multi-frame extrapolation — two different
jobs, two different (both correct) choices of the same underlying
CrowdPressureField.

============================================================
DELIBERATELY NOT THE PROBLEM STATEMENT'S "10 MINUTES BEFORE" FIGURE
============================================================
`PREDICTION_HORIZON_SECONDS` defaults to 30 seconds, extrapolated from a
`PREDICTIVE_WINDOW_SECONDS`-wide (default 10s) rolling window of recent
data. This is NOT an attempt at the problem statement's "predict crowd
crush risk 10 minutes before it happens" aspiration — confidently
linear-extrapolating 10 minutes forward from a 10-second window of noisy
real-world pressure data would be statistically indefensible, and this
module makes no such claim anywhere. The spec's "10 minutes before" is a
SYSTEM-LEVEL goal meant to be achieved through SUSTAINED trend detection
and risk-state persistence over time (the not-yet-built Risk State +
Trigger Engine's job, watching this projection — and much more — across
many frames) — not a literal guarantee from any single call to this
lightweight, single-window linear fit. Do not conflate the two.
"""

import logging
from dataclasses import dataclass

import numpy as np

from app.core.config import settings
from app.pipeline.crowd_pressure import CrowdPressureField

logger = logging.getLogger(__name__)

# Minimum (timestamp, mean_pressure) pairs required before a least-squares
# line fit is attempted. 2 points would produce a "fit" that is really
# just connecting two dots (R^2 trivially 1.0, no genuine trend evidence);
# 3 is the smallest window size where a linear fit is actually
# distinguishable from that degenerate case. Same "don't fabricate a
# result from too little data" principle as density.py's
# MIN_POINTS_FOR_RELIABLE_ESTIMATION=3 (Phase 9) — not independently
# configurable, this is a mathematical floor, not a tunable.
_MIN_DATA_POINTS_FOR_PROJECTION = 3


@dataclass
class PredictiveProjection:
    frame_number: int
    timestamp_seconds: float  # of the frame the projection was computed from
    horizon_seconds: float  # matches settings.PREDICTION_HORIZON_SECONDS
    projected_pressure: float  # clamped to >= 0.0 (decision #8)
    r_squared: float  # 0-1, least-squares goodness-of-fit of the linear trend
    window_seconds_used: float  # may be less than PREDICTIVE_WINDOW_SECONDS early in a video
    data_points_used: int


class PressureProjector:
    """STATEFUL — construct one fresh instance per video/session, update()
    incrementally as each new frame's CrowdPressureField arrives, and
    NEVER reuse across two different videos (same hard rule as Phase 10's
    BottleneckDetector/ReverseFlowDetector).

    Maintains a TIME-based (not frame-count-based) rolling window of
    (timestamp_seconds, mean_pressure) pairs spanning the most recent
    PREDICTIVE_WINDOW_SECONDS of real elapsed time — deliberately not a
    fixed frame count, since a video's fps (and therefore how many frames
    fall within a given time span) can vary.
    """

    def __init__(self) -> None:
        self._window: list[tuple[float, float]] = []
        # Observable per the project's "never fail silently" principle
        # (see `update()`'s non-monotonic-timestamp guard below) — reflects
        # only the MOST RECENT call to update(), overwritten every call.
        self.last_update_rejected: bool = False

    def update(self, pressure: CrowdPressureField) -> PredictiveProjection | None:
        if self._window and pressure.timestamp_seconds <= self._window[-1][0]:
            # A PressureProjector instance, like a Tracker instance, must
            # only ever be fed ONE continuous, monotonically-increasing
            # pass through one video (mirrors the ByteTrackAdapter
            # timestamp-monotonicity constraint already logged in
            # DECISIONS.md's "Implementation-Discovered Constraints"
            # table). Silently accepting an earlier-or-equal timestamp here
            # is exactly what previously let the time-based prune below
            # under-prune and the window balloon to ~2x its configured
            # size under replay (Phase 11->12 bridging check finding) —
            # reject instead of accepting, and leave the existing window
            # completely untouched.
            logger.warning(
                "PressureProjector.update() received a non-monotonic "
                "timestamp (%.6f <= previous %.6f) — rejecting this update "
                "and leaving the existing window untouched. A "
                "PressureProjector instance must only be fed one "
                "continuous, monotonically-increasing pass through one "
                "video.",
                pressure.timestamp_seconds,
                self._window[-1][0],
            )
            self.last_update_rejected = True
            return None

        self.last_update_rejected = False
        self._window.append((pressure.timestamp_seconds, pressure.mean_pressure))

        latest_timestamp = self._window[-1][0]
        cutoff = latest_timestamp - settings.PREDICTIVE_WINDOW_SECONDS
        self._window = [(t, p) for t, p in self._window if t >= cutoff]

        if len(self._window) < _MIN_DATA_POINTS_FOR_PROJECTION:
            # Never fabricate a trend from too little data.
            return None

        timestamps = np.array([t for t, _ in self._window])
        pressures = np.array([p for _, p in self._window])

        slope, intercept = np.polyfit(timestamps, pressures, deg=1)

        predicted = slope * timestamps + intercept
        ss_res = float(np.sum((pressures - predicted) ** 2))
        ss_tot = float(np.sum((pressures - pressures.mean()) ** 2))
        # ss_tot == 0 means every observed value in the window was
        # identical (a perfectly flat series) — the best-fit line is that
        # same flat value, with zero residual, i.e. a perfect (1.0) fit.
        r_squared = 1.0 if ss_tot == 0 else 1.0 - ss_res / ss_tot
        r_squared = float(np.clip(r_squared, 0.0, 1.0))

        target_time = latest_timestamp + settings.PREDICTION_HORIZON_SECONDS
        projected = slope * target_time + intercept
        # Decision #8: Crowd Pressure is mathematically non-negative
        # (density x variance, both >= 0) — clamp away any nonsensical
        # negative extrapolation from a downward trend.
        projected_pressure = max(0.0, float(projected))

        return PredictiveProjection(
            frame_number=pressure.frame_number,
            timestamp_seconds=pressure.timestamp_seconds,
            horizon_seconds=settings.PREDICTION_HORIZON_SECONDS,
            projected_pressure=projected_pressure,
            r_squared=r_squared,
            window_seconds_used=latest_timestamp - timestamps[0],
            data_points_used=len(self._window),
        )
