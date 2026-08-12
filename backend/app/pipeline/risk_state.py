"""Risk State Machine — roadmap Phase 13, part 1. Classifies Phase 11's
continuous, per-frame `risk_score` (0-100) into a discrete
NORMAL/ELEVATED/CRITICAL/INCIDENT state, using BOTH temporal persistence
and hysteresis so "a single anomalous frame never triggers escalation"
(master spec §14, verbatim).

============================================================
RESOLUTION 1 — classifies risk_score, NOT raw Crowd Pressure (units problem)
============================================================
§14 frames risk states as "operationalizing the underlying Crowd Pressure
thresholds" with literature-cited real-world values (~0.02/0.04 s^-2). As
established since Phase 9's Critical Units Disclosure (carried through
Phases 10-12, see DECISIONS.md), this project's computed Crowd Pressure is
PIXEL-space, not the literature's SI units — those literal numbers are
inapplicable here. This module sidesteps the mismatch the same way every
phase since Phase 9 has: it classifies state from Phase 11's `risk_score`
(`RiskScoreResult.risk_score`, already 0-100 normalized/dimensionless, and
already weighted 50% toward pressure by Phase 11's own design — see
risk_score.py's RISK_SCORE_WEIGHT_PRESSURE), never from a raw
CrowdPressureField value directly.

============================================================
RESOLUTION 2 — INCIDENT is defined but structurally UNREACHABLE this phase
============================================================
Per §14's own state diagram, CRITICAL -> INCIDENT requires "Decision
Intelligence confirms" — a component that does not exist until a much
later phase. `RiskState` defines the full eventual enum (same honest
pattern as Phase 4's `SessionStatus` shipping unreachable future values),
but `RiskStateMachine` has NO code path that can ever produce INCIDENT —
its ceiling in this phase is CRITICAL. Proven by
`test_risk_state.py::test_incident_state_is_structurally_unreachable_this_phase`.

CRITICAL NAMING DISAMBIGUATION: `RiskState.INCIDENT` is a classification
LABEL within THIS state machine only. It is NOT the same thing as the
future "Incident" DATABASE ENTITY (§19, roadmap Phase 18) with its own
DETECTED/ACTIVE/RESOLVED lifecycle and operator actions
(acknowledge/dismiss/resolve/escalate, §20) — none of that exists here.
Do not conflate the two when reading this module or `risk_events`.
"""

import enum
from dataclasses import dataclass

from app.core.config import settings
from app.pipeline.crowd_metrics import CrowdMetrics


class RiskState(str, enum.Enum):
    NORMAL = "NORMAL"
    ELEVATED = "ELEVATED"
    CRITICAL = "CRITICAL"
    # Defined for completeness (Resolution 2) — no code path in this phase
    # can ever produce this value. Reachable only once a future Decision
    # Intelligence phase confirms a CRITICAL episode as a genuine incident.
    INCIDENT = "INCIDENT"


# Public (not module-private) — TriggerEngine (decision #5/#6) imports this
# directly to compare severity levels without redefining its own copy.
RISK_STATE_RANK = {
    RiskState.NORMAL: 0,
    RiskState.ELEVATED: 1,
    RiskState.CRITICAL: 2,
    RiskState.INCIDENT: 3,
}


@dataclass
class RiskStateResult:
    frame_number: int
    timestamp_seconds: float
    state: RiskState
    risk_score: float  # passed through from RiskScoreResult, for traceability
    # Diagnostic: how many consecutive frames the CURRENTLY PENDING candidate
    # transition (if any) has been building. 0 when no candidate is pending.
    frames_in_current_candidate_transition: int
    # Decision #4: True once risk_score has sustained-persisted (for
    # RISK_STATE_PERSISTENCE_FRAMES consecutive frames) at/above
    # RISK_INCIDENT_THRESHOLD while ALREADY CRITICAL. Informational metadata
    # only — this phase takes no action on it (see decision #4's own
    # reasoning below); a future Decision Intelligence phase may consume it.
    incident_threshold_crossed: bool
    # True ONLY on the exact update() call a confirmed transition occurs.
    state_changed_this_frame: bool


def _evaluate_candidate(state: RiskState, risk_score: float) -> RiskState | None:
    """Decision #2/#3: which state (if any) `risk_score` is currently
    "pushing toward" given the CURRENT confirmed state. Returns None when
    risk_score sits inside the current state's stable band (including the
    hysteresis gap between a rise and fall threshold)."""
    rise_elevated = settings.RISK_ELEVATED_THRESHOLD
    rise_critical = settings.RISK_CRITICAL_THRESHOLD
    fall_elevated = rise_elevated - settings.RISK_STATE_FALL_HYSTERESIS_MARGIN
    fall_critical = rise_critical - settings.RISK_STATE_FALL_HYSTERESIS_MARGIN

    if state == RiskState.NORMAL:
        if risk_score > rise_elevated:
            return RiskState.ELEVATED
        return None

    if state == RiskState.ELEVATED:
        if risk_score > rise_critical:
            return RiskState.CRITICAL
        if risk_score < fall_elevated:
            return RiskState.NORMAL
        return None

    if state == RiskState.CRITICAL:
        # No upward candidate exists here at all (Resolution 2) — CRITICAL
        # is this phase's ceiling, structurally, not just by an unmet
        # threshold check.
        if risk_score < fall_critical:
            return RiskState.ELEVATED
        return None

    # state == RiskState.INCIDENT: de-escalation FROM Incident is explicitly
    # out of scope this phase (decision #3) — it requires the real Incident
    # Manager/persisted-incident architecture that doesn't exist until Phase
    # 18. Moot in practice since INCIDENT is unreachable (Resolution 2), but
    # documented for completeness: no candidate is ever evaluated here.
    return None


class RiskStateMachine:
    """STATEFUL (decision #10) — construct ONE fresh instance per
    video/session, feed it exactly one continuous pass via `update()`, and
    never reuse it across two different videos/sessions. Same hard rule as
    every other stateful pipeline component since Phase 7 (Tracker,
    BottleneckDetector, ReverseFlowDetector, PressureProjector).

    Initial state is NORMAL.
    """

    def __init__(self) -> None:
        self._state = RiskState.NORMAL
        self._candidate_target: RiskState | None = None
        self._candidate_count = 0
        # Decision #4's own persistence counter — deliberately SEPARATE from
        # the transition-candidate counter above, since crossing
        # RISK_INCIDENT_THRESHOLD while CRITICAL never drives a transition
        # (Resolution 2) and must not interfere with CRITICAL's own
        # de-escalation candidate tracking.
        self._incident_flag_count = 0

    def update(self, crowd_metrics: CrowdMetrics) -> RiskStateResult:
        risk_score = crowd_metrics.risk_score.risk_score

        candidate = _evaluate_candidate(self._state, risk_score)
        if candidate is None:
            self._candidate_target = None
            self._candidate_count = 0
        elif candidate == self._candidate_target:
            self._candidate_count += 1
        else:
            self._candidate_target = candidate
            self._candidate_count = 1

        state_changed = False
        if (
            self._candidate_target is not None
            and self._candidate_count >= settings.RISK_STATE_PERSISTENCE_FRAMES
        ):
            self._state = self._candidate_target
            state_changed = True
            self._candidate_target = None
            self._candidate_count = 0
            # A confirmed transition starts a fresh diagnostic window for
            # decision #4's flag — an old count accrued in a PRIOR CRITICAL
            # episode must not silently carry into a new one.
            self._incident_flag_count = 0

        # Decision #4: diagnostic-only, sustained-CRITICAL incident-threshold
        # tracking. Uses the SAME persistence config as state transitions
        # (RISK_STATE_PERSISTENCE_FRAMES) — a deliberate reuse rather than a
        # new config surface for a metadata-only flag (see DECISIONS.md).
        if self._state == RiskState.CRITICAL and risk_score >= settings.RISK_INCIDENT_THRESHOLD:
            self._incident_flag_count += 1
        else:
            self._incident_flag_count = 0
        incident_threshold_crossed = (
            self._incident_flag_count >= settings.RISK_STATE_PERSISTENCE_FRAMES
        )

        return RiskStateResult(
            frame_number=crowd_metrics.frame_number,
            timestamp_seconds=crowd_metrics.timestamp_seconds,
            state=self._state,
            risk_score=risk_score,
            frames_in_current_candidate_transition=self._candidate_count,
            incident_threshold_crossed=incident_threshold_crossed,
            state_changed_this_frame=state_changed,
        )
