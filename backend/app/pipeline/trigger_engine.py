"""Trigger Engine — roadmap Phase 13, part 2. Decides WHEN a future Vision
Intelligence (VLM) / Decision Intelligence (LLM) invocation should happen.
Per master spec §14: "The VLM and LLM are never run on every frame; the
trigger architecture exists specifically for CPU feasibility and
intelligent compute allocation."

SCOPE BOUNDARY (read before assuming this module does more than it does):
NOTHING in this module invokes a VLM or LLM. `TriggerEngine.evaluate()`
only returns a `TriggerDecision` describing what SHOULD happen — the actual
invocation is a later phase's job (Vision Intelligence does not exist yet).

Component contract (§28, frozen): `TriggerEngine.evaluate(crowd_metrics,
risk_state, operator_requested=False) -> TriggerDecision`. `risk_state`
must already be computed by `RiskStateMachine` FIRST and passed in — this
engine never classifies risk itself.

============================================================
DECISION #9 — trigger priority when multiple conditions are true at once
============================================================
OPERATOR > RISK > FALLBACK > NONE, checked in that exact order every
`evaluate()` call. An explicit human request always wins; a genuine risk
escalation next; a routine periodic check last.

============================================================
DECISION #5 — RISK fires on the state machine's OWN confirmed upward
transition, not a second independent threshold
============================================================
Reuses `RiskStateResult.state_changed_this_frame` + a rank comparison
against the state seen on the PREVIOUS `evaluate()` call, rather than
re-deriving "is this an escalation" from a second, separately-tunable
threshold that could disagree with `RiskStateMachine` right at a boundary.

============================================================
DECISION #6 — RISK trigger cooldown (VLM_COOLDOWN) with an override
============================================================
Once RISK fires, further RISK firing is suppressed for VLM_COOLDOWN
seconds — UNLESS the risk state escalates to a NEW, HIGHER level during
that cooldown window (e.g. ELEVATED->CRITICAL while still cooling down
from an earlier NORMAL->ELEVATED firing). A genuinely worse escalation
must never be silently swallowed by a cooldown that exists only to
prevent redundant re-triggering at the SAME severity level. FALLBACK and
OPERATOR are NOT subject to this cooldown at all (FALLBACK has its own
independent interval; OPERATOR is a deliberate human action that must
never be silently suppressed).

============================================================
DECISION #7 — FALLBACK fires on a real-time interval, independent of risk
============================================================
Uses `FALLBACK_ANALYSIS_INTERVAL` (seconds, via `timestamp_seconds` — NOT
frame count, so behavior is fps-independent) — fires even if risk state
stays NORMAL for the entire session, per §14's explicit "independent of
risk" wording. Never suppressed by RISK's cooldown.

============================================================
DECISION #8 — OPERATOR is plumbing only, not a complete feature
============================================================
`operator_requested: bool = False` is honored correctly when set (always
wins, unconditionally), but there is no real API/UI path yet for an
operator to actually set it — that is dashboard integration, a later
phase. This phase only implements the correct interface for it.
"""

import enum
from dataclasses import dataclass

from app.core.config import settings
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.risk_state import RISK_STATE_RANK, RiskState, RiskStateResult


class TriggerType(str, enum.Enum):
    NONE = "NONE"
    RISK = "RISK"
    FALLBACK = "FALLBACK"
    OPERATOR = "OPERATOR"


@dataclass
class TriggerDecision:
    frame_number: int
    timestamp_seconds: float
    trigger_type: TriggerType
    reason: str  # human-readable, e.g. "risk state escalated NORMAL->ELEVATED"


class TriggerEngine:
    """STATEFUL (decision #10) — construct ONE fresh instance per
    video/session, never reuse across two different videos/sessions. Same
    hard rule as `RiskStateMachine` and every other stateful pipeline
    component since Phase 7.

    Tracks, internally: the risk state last observed (to detect a fresh
    upward transition), the timestamp + state of the last RISK firing (for
    cooldown + override), the timestamp of the last FALLBACK firing (for
    its own independent interval), and the session's first-seen timestamp
    (FALLBACK's baseline reference before it has ever fired).
    """

    def __init__(self) -> None:
        self._last_known_state: RiskState = RiskState.NORMAL
        self._last_risk_trigger_timestamp: float | None = None
        self._last_risk_trigger_state: RiskState | None = None
        self._last_fallback_trigger_timestamp: float | None = None
        self._session_start_timestamp: float | None = None

    def evaluate(
        self,
        crowd_metrics: CrowdMetrics,
        risk_state: RiskStateResult,
        operator_requested: bool = False,
    ) -> TriggerDecision:
        frame_number = risk_state.frame_number
        timestamp_seconds = risk_state.timestamp_seconds

        if self._session_start_timestamp is None:
            self._session_start_timestamp = timestamp_seconds

        is_upward_transition = (
            risk_state.state_changed_this_frame
            and RISK_STATE_RANK[risk_state.state] > RISK_STATE_RANK[self._last_known_state]
        )

        decision: TriggerDecision | None = None

        if operator_requested:
            decision = TriggerDecision(
                frame_number, timestamp_seconds, TriggerType.OPERATOR, "operator requested"
            )
        elif is_upward_transition:
            cooldown_active = (
                self._last_risk_trigger_timestamp is not None
                and (timestamp_seconds - self._last_risk_trigger_timestamp) < settings.VLM_COOLDOWN
            )
            is_override = cooldown_active and (
                self._last_risk_trigger_state is not None
                and RISK_STATE_RANK[risk_state.state] > RISK_STATE_RANK[self._last_risk_trigger_state]
            )
            if not cooldown_active or is_override:
                reason = (
                    f"risk state escalated {self._last_known_state.value}->{risk_state.state.value}"
                )
                if is_override:
                    reason += " (cooldown override: new higher escalation)"
                decision = TriggerDecision(
                    frame_number, timestamp_seconds, TriggerType.RISK, reason
                )
                self._last_risk_trigger_timestamp = timestamp_seconds
                self._last_risk_trigger_state = risk_state.state

        if decision is None:
            fallback_reference = (
                self._last_fallback_trigger_timestamp
                if self._last_fallback_trigger_timestamp is not None
                else self._session_start_timestamp
            )
            elapsed = timestamp_seconds - fallback_reference
            if elapsed >= settings.FALLBACK_ANALYSIS_INTERVAL:
                decision = TriggerDecision(
                    frame_number,
                    timestamp_seconds,
                    TriggerType.FALLBACK,
                    f"fallback interval elapsed ({elapsed:.1f}s since last)",
                )
                self._last_fallback_trigger_timestamp = timestamp_seconds

        if decision is None:
            decision = TriggerDecision(frame_number, timestamp_seconds, TriggerType.NONE, "none")

        self._last_known_state = risk_state.state
        return decision
