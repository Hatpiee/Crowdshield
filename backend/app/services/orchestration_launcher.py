"""Launches a session's real processing pipeline on a background thread —
Phase 20, Decision A. Plain `threading` only (no Celery/RQ/Redis/message
broker of any kind, per the constitutional ban this phase reaffirms).

Called AFTER `session_service.start_session()` has already succeeded — that
existing function's own logic (validate CREATED, transition to QUEUED,
create a PENDING ProcessingRun) is left completely unchanged; this module
is purely additive.
"""

import threading
import uuid

from app.pipeline.analysis_orchestrator import AnalysisOrchestrator

# Thread names carry this prefix so tests can deterministically find and
# join every orchestrator thread a test spawned (see conftest.py's
# `_join_orchestrator_threads` autouse fixture) without any production-side
# bookkeeping list that would otherwise grow unboundedly over a long-running
# server's lifetime.
THREAD_NAME_PREFIX = "analysis-orchestrator-"


def launch_session_processing(session_id: uuid.UUID) -> threading.Thread:
    """Constructs an AnalysisOrchestrator and spawns it on a new thread,
    returning immediately WITHOUT waiting for it — the caller (the /start
    route) must not block on real processing.

    `daemon=False`: a started evidence/decision chain represents real work
    already substantially underway (this project's own evidentiary
    philosophy, Decision D) — it must not be silently killed just because
    the interpreter is shutting down (e.g. a clean `uvicorn --reload`
    restart mid-run). The tradeoff, accepted here, is that a hung Loop A/B
    thread could in principle delay process exit; every real network call
    this pipeline makes already has its own configured timeout
    (VLM/LLM/VERIFIER_REQUEST_TIMEOUT_SECONDS), so this is bounded, not
    unbounded.

    Returns the spawned Thread (ignored by real callers — the route never
    joins it — but returned so tests can join it deterministically).
    """
    orchestrator = AnalysisOrchestrator(session_id)
    thread = threading.Thread(
        target=orchestrator.run,
        name=f"{THREAD_NAME_PREFIX}{session_id}",
        daemon=False,
    )
    thread.start()
    return thread
