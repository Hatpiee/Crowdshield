"""Severity gate — Phase 18, Decision D.

Consistent with how TriggerEngine (not VisionModel) owns "should this even
run" throughout this project, `Verifier.verify()` itself does not check
`decision.outcome` — the CALLER checks `should_verify()` before deciding
whether to invoke `Verifier.verify()` at all (see
verification_service.py's `run_verification_if_warranted`).
"""

from app.pipeline.decision_result import DecisionOutcome, DecisionResult


def should_verify(decision: DecisionResult) -> bool:
    # §18: verification is severity-gated, "not run on every event, to
    # avoid wasting CPU/latency on routine cases" — gated on the single
    # highest-priority outcome only (§8's "highest-priority escalations
    # only"). WATCH/NO_INCIDENT/ABSTAIN never trigger a second LLM pass.
    return decision.outcome == DecisionOutcome.INCIDENT
