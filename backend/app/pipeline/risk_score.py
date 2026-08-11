"""Composite Risk Score (master spec §12 OUTPUT description: normalized
0-100) — roadmap Phase 11's final piece, combining Phase 9's Crowd
Pressure with Phase 10's Congestion/Bottleneck/Reverse Flow.

============================================================
DESIGN RATIONALE (engineering judgment — the master spec gives an exact
formula for Crowd Pressure but does NOT specify how to combine these four
signals into one score; logged in DECISIONS.md)
============================================================
1. Each signal is converted to its OWN 0-100 sub-score independently (see
   the four `_*_subscore` helpers below), then combined via a WEIGHTED
   AVERAGE with CONFIGURABLE weights (RISK_SCORE_WEIGHT_*).
2. Pressure gets the LARGEST weight (default 0.5) — §12 calls Crowd
   Pressure "the single most heavily validated decision in the entire
   project"; the other three are secondary contributing signals.
3. A sub-score that's genuinely unavailable this frame (only Bottleneck
   can be — its window may not have filled yet, per Phase 10) is NEVER
   silently treated as 0 in the weighted average — that would quietly
   present "no data yet" as "confirmed safe," violating §12's own failure-
   mode principle. Its weight is instead redistributed PROPORTIONALLY
   among whichever sub-scores ARE available this frame (see
   `_redistribute_weights` below for the exact math with a worked
   example). `contributing_signals` records which signals actually fed
   into a given frame's score — this is the same "confidence vs.
   completeness" distinction the spec draws explicitly at §16's Evidence
   Package, applied one layer earlier in the pipeline than the spec
   describes it there.
4. `confidence` is PROPAGATED, never invented, per the project's
   constitutional "Confidence Propagation, Never Invention" principle — it
   is exactly `DensityField.estimation_confidence` (Phase 9), the only
   genuine statistical-estimation-reliability measurement among these
   signals. This module computes no separate, independently-derived
   confidence formula.
"""

from dataclasses import dataclass, field

import numpy as np

from app.core.config import settings
from app.pipeline.bottleneck import BottleneckField
from app.pipeline.congestion import CongestionField
from app.pipeline.crowd_pressure import CrowdPressureField
from app.pipeline.density import DensityField
from app.pipeline.reverse_flow import ReverseFlowField

# Canonical signal order — used for `contributing_signals` so it's stable
# and readable regardless of dict insertion order.
_SIGNAL_ORDER = ("pressure", "congestion", "bottleneck", "reverse_flow")


@dataclass
class RiskScoreResult:
    frame_number: int
    timestamp_seconds: float
    risk_score: float  # 0-100
    confidence: float  # 0-1, propagated from DensityField.estimation_confidence
    contributing_signals: list[str] = field(default_factory=list)
    sub_scores: dict[str, float] = field(default_factory=dict)  # only available signals


def _pressure_subscore(pressure: CrowdPressureField) -> float:
    """Decision #1: uses max_pressure (worst-case-sensitive — a single
    crushing cell matters even surrounded by calm ones), NOT mean_pressure.
    PIXEL-SPACE-native reference constant, same units caveat as everything
    since Phase 9."""
    raw = (pressure.max_pressure / settings.PRESSURE_SCORE_REFERENCE_PX) * 100.0
    return min(100.0, raw)


def _congestion_subscore(congestion: CongestionField) -> float:
    """Decision #2: congested_cell_fraction is already self-normalizing
    (0-1, Phase 10) — direct scale to 0-100, no new threshold needed."""
    return congestion.congested_cell_fraction * 100.0


def _bottleneck_subscore(bottleneck: BottleneckField) -> float | None:
    """Decision #3: inverts Phase 10's convergence ratio (lower = more
    danger) using the grid's strongest (minimum) value. Returns None if
    every cell's ratio is NaN (degenerate grid with no computable spread
    anywhere) — genuinely no signal, not fabricated as 0."""
    finite_scores = bottleneck.bottleneck_score_grid[
        ~np.isnan(bottleneck.bottleneck_score_grid)
    ]
    if finite_scores.size == 0:
        return None
    min_ratio = float(finite_scores.min())
    # min_ratio >= 0 always (a spread ratio can't be negative), so
    # (1 - min_ratio) is naturally <= 100 after scaling; max(0, ...) only
    # guards the dispersal case (ratio > 1 -> would-be-negative score).
    return max(0.0, (1.0 - min_ratio)) * 100.0


def _reverse_flow_subscore(reverse_flow: ReverseFlowField) -> float:
    """Decision #4: reverse_flow_cell_fraction is already self-normalizing
    (0-1, Phase 10) — direct scale to 0-100, no new threshold needed."""
    return reverse_flow.reverse_flow_cell_fraction * 100.0


def _redistribute_weights(available_signals: list[str]) -> dict[str, float]:
    """Decision #5: proportionally redistributes the configured
    RISK_SCORE_WEIGHT_* values across only the signals actually available
    this frame, so a missing sub-score is never implicitly weighted as 0.

    WORKED EXAMPLE (using the default weights: pressure=0.5,
    congestion=0.2, bottleneck=0.2, reverse_flow=0.1 — sums to 1.0):
    If `bottleneck` is unavailable this frame, the remaining configured
    weights are {pressure: 0.5, congestion: 0.2, reverse_flow: 0.1},
    summing to 0.8. Redistributed (each divided by that 0.8 total):
        pressure     -> 0.5 / 0.8 = 0.625
        congestion   -> 0.2 / 0.8 = 0.25
        reverse_flow -> 0.1 / 0.8 = 0.125
    (0.625 + 0.25 + 0.125 = 1.0 exactly — the missing signal's 0.2 share
    has been proportionally absorbed by the other three, not dropped and
    not assigned to a fabricated 0 sub-score.)
    """
    configured_weights = {
        "pressure": settings.RISK_SCORE_WEIGHT_PRESSURE,
        "congestion": settings.RISK_SCORE_WEIGHT_CONGESTION,
        "bottleneck": settings.RISK_SCORE_WEIGHT_BOTTLENECK,
        "reverse_flow": settings.RISK_SCORE_WEIGHT_REVERSE_FLOW,
    }
    available_weights = {
        signal: configured_weights[signal] for signal in available_signals
    }
    total = sum(available_weights.values())
    return {signal: weight / total for signal, weight in available_weights.items()}


def compute_risk_score(
    density: DensityField,
    pressure: CrowdPressureField,
    congestion: CongestionField,
    bottleneck: BottleneckField | None,
    reverse_flow: ReverseFlowField,
) -> RiskScoreResult:
    sub_scores: dict[str, float] = {
        "pressure": _pressure_subscore(pressure),
        "congestion": _congestion_subscore(congestion),
        "reverse_flow": _reverse_flow_subscore(reverse_flow),
    }

    if bottleneck is not None:
        bottleneck_subscore = _bottleneck_subscore(bottleneck)
        if bottleneck_subscore is not None:
            sub_scores["bottleneck"] = bottleneck_subscore
    # bottleneck is None (window not yet filled) or produced no computable
    # signal -> genuinely unavailable this frame; NOT added to sub_scores,
    # NOT treated as 0. Its weight is redistributed below.

    contributing_signals = [signal for signal in _SIGNAL_ORDER if signal in sub_scores]
    weights = _redistribute_weights(contributing_signals)

    risk_score = sum(sub_scores[signal] * weights[signal] for signal in contributing_signals)
    risk_score = float(np.clip(risk_score, 0.0, 100.0))

    return RiskScoreResult(
        frame_number=density.frame_number,
        timestamp_seconds=density.timestamp_seconds,
        risk_score=risk_score,
        confidence=density.estimation_confidence,
        contributing_signals=contributing_signals,
        sub_scores=sub_scores,
    )
