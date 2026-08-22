"""Semantic Admission Queue — Semantic Admission Control phase (the phase
following Sprint-0 CPU/Concurrency/Ollama Load Validation).

REPLACES the prior "drop-on-cap" behavior in AnalysisOrchestrator (a
`threading.Semaphore(MAX_CONCURRENT_SEMANTIC_ANALYSES)` with a non-blocking
acquire that DROPPED any trigger firing while the cap was reached) with a
bounded, freshness-aware admission queue. See DECISIONS.md, "Semantic
Admission Control" for the full real-measured rationale — the Sprint-0
benchmark's own real drop-rate progression under increasing trigger
frequency (LOW 20% / MODERATE 60% / HIGH 70%) is the concrete evidence this
phase responds to.

============================================================
DESIGN (deliberately the SMALLEST policy that solves the measured problem)
============================================================
- `max_concurrent` long-lived WORKER threads (mirrors the prior semaphore's
  own cap exactly — MAX_CONCURRENT_SEMANTIC_ANALYSES), started once at
  construction, stopped once at close(). Each worker pulls one task at a
  time and runs it to completion — a fixed-size worker pool already caps
  concurrency by itself; no separate semaphore object is needed.
- A SMALL bounded `collections.deque` (max length SEMANTIC_QUEUE_MAX_DEPTH,
  enforced manually below so eviction can be counted — see submit()) holds
  tasks waiting for a free worker.
- Workers pop from the RIGHT (LIFO — freshest first). A queued task whose
  own wait time has already exceeded SEMANTIC_QUEUE_STALENESS_SECONDS by
  the time a worker reaches it is DROPPED (not executed) — this is what
  keeps a small bounded queue from ever becoming a multi-minute backlog of
  now-irrelevant work (an old VLM request for timestamp 3s is useless once
  the video has reached 40s), per the governing task's explicit "a solution
  that reduces drops to 0% but creates 5-minute semantic backlogs is NOT
  successful" requirement.
- `submit()` is 100% non-blocking (a lock-protected deque append + a
  condition-variable notify — no I/O, no waiting on another thread) — Loop
  A NEVER blocks on semantic work, preserved exactly as it was before this
  phase (the old semaphore's `acquire(blocking=False)` was already
  non-blocking too; this keeps that same guarantee under a richer policy).

============================================================
FRESHNESS POLICY, IN ONE SENTENCE
============================================================
Between two competing pieces of pending work, prefer the newer one — both
when the queue is full (submit() evicts the OLDEST queued item to admit a
new one) and when a worker is choosing what to run next (workers serve
LIFO, freshest first, and skip anything that has aged past staleness).

============================================================
NOT DONE (explicit non-goals, per the governing task)
============================================================
NOT an unbounded queue. NOT a priority scheduler beyond simple recency. NOT
asyncio. NOT a second process/Redis/Celery/Kafka queue — this is a single
Python object with a bounded in-memory deque and stdlib threading
primitives, the same class of mechanism (`threading.Semaphore`) it
replaces. NOT a guarantee that every submitted task eventually runs — a
task may be displaced (capacity) or skipped (staleness) by design; the
guarantee this phase actually provides is that the WORKER POOL itself never
stalls or deadlocks, and Loop A is never blocked.

============================================================
"no starvation" — what that means concretely here
============================================================
This does NOT mean every individual submitted task is guaranteed to run —
a genuinely stale/superseded task is deliberately dropped, by design (see
FRESHNESS POLICY above). What IS guaranteed: an available worker always
makes progress on the newest available work (never idles while there is
non-stale work waiting), and no task, once STARTED, is ever preempted or
starved of CPU by this queue itself (that is a separate, real question
about OS-level thread scheduling under CPU contention — see
scripts/benchmark_sprint0_cpu.py's Loop-A stage-latency diagnostic and
DECISIONS.md's "Loop-A Tail Latency" entry for that investigation).
"""

import logging
import threading
import time
from collections import deque
from dataclasses import dataclass, field
from typing import Callable, Optional

logger = logging.getLogger(__name__)


@dataclass
class SemanticQueueMetrics:
    """In-memory only (no per-frame/per-task DB writes, per this project's
    own "no per-frame DB writes" constitutional constraint) — read via
    `SemanticAdmissionQueue.metrics_snapshot()`. Same "never fabricate,
    count only what genuinely happened" discipline already established by
    scripts/benchmark_sprint0_cpu.py's own FailureCounter."""

    enqueued_total: int = 0
    dequeued_total: int = 0  # actually STARTED (popped by a worker, not stale)
    dropped_capacity_total: int = 0  # evicted from the deque by a newer arrival
    dropped_stale_total: int = 0  # popped by a worker, but too old to bother starting
    completed_total: int = 0  # task.run() returned without raising
    failed_total: int = 0  # task.run() raised — see module docstring; expected to stay 0
    active_count: int = 0
    queue_depth: int = 0
    _queue_wait_seconds: list = field(default_factory=list, repr=False)

    def record_queue_wait(self, seconds: float) -> None:
        self._queue_wait_seconds.append(seconds)

    def snapshot(self) -> dict:
        waits = list(self._queue_wait_seconds)
        return {
            "enqueued_total": self.enqueued_total,
            "dequeued_total": self.dequeued_total,
            "dropped_capacity_total": self.dropped_capacity_total,
            "dropped_stale_total": self.dropped_stale_total,
            "completed_total": self.completed_total,
            "failed_total": self.failed_total,
            "active_count": self.active_count,
            "queue_depth": self.queue_depth,
            "queue_wait_seconds_mean": (sum(waits) / len(waits)) if waits else None,
            "queue_wait_seconds_max": max(waits) if waits else None,
            "queue_wait_seconds_count": len(waits),
        }


@dataclass
class _QueuedTask:
    enqueued_at: float
    trigger_timestamp_seconds: float
    run: Callable[[], None]
    label: str = ""


class SemanticAdmissionQueue:
    """Constructed ONCE per session (same lifecycle discipline as every
    other per-session stateful component in analysis_orchestrator.py — the
    prior `threading.Semaphore` it replaces was already scoped this way).
    `max_concurrent` mirrors that semaphore's own cap exactly;
    `max_queue_depth`/`staleness_seconds` are this phase's new knobs (see
    config.py for their real-measured rationale). Worker threads are
    daemon=True as a defensive safety net against orphaning a test/dev
    process that never calls close() — the PRODUCTION path
    (AnalysisOrchestrator.run()) always calls close() on every terminal
    path (COMPLETED/CANCELLED/FAILED), which is the real mechanism that
    keeps threads from being orphaned (see Phase G / DECISIONS.md)."""

    def __init__(
        self,
        max_concurrent: int,
        max_queue_depth: int,
        staleness_seconds: float,
    ) -> None:
        self._max_queue_depth = max_queue_depth
        self._staleness_seconds = staleness_seconds
        self._queue: deque[_QueuedTask] = deque()
        self._condition = threading.Condition()
        self._closing = False
        self.metrics = SemanticQueueMetrics()
        self._workers = [
            threading.Thread(
                target=self._worker_loop, name=f"semantic-admission-worker-{i}", daemon=True,
            )
            for i in range(max(1, max_concurrent))
        ]
        for worker in self._workers:
            worker.start()

    def submit(self, run: Callable[[], None], trigger_timestamp_seconds: float, label: str = "") -> None:
        """Non-blocking. Called from Loop A's own thread — must never wait
        on I/O or another thread's completion. Returns immediately in
        every case (accepted, capacity-evicted-someone-else, or
        rejected-because-closing)."""
        task = _QueuedTask(
            enqueued_at=time.monotonic(), trigger_timestamp_seconds=trigger_timestamp_seconds,
            run=run, label=label,
        )
        with self._condition:
            if self._closing:
                # Session already ending — a brand-new trigger arriving this
                # late is not meaningfully different from one that arrived
                # just before shutdown and would have been stale-dropped;
                # treat it the same way rather than starting fresh work
                # after Loop A has already stopped.
                self.metrics.dropped_stale_total += 1
                logger.warning(
                    "SemanticAdmissionQueue: submit() after close() — dropped (label=%s)", label,
                )
                return
            if len(self._queue) >= self._max_queue_depth:
                evicted = self._queue.popleft()  # oldest queued item, displaced by this fresher one
                self.metrics.dropped_capacity_total += 1
                logger.warning(
                    "SemanticAdmissionQueue: queue at capacity (%d) — evicted OLDEST queued "
                    "task (label=%s, waited=%.2fs so far) to admit a newer one (label=%s)",
                    self._max_queue_depth, evicted.label, time.monotonic() - evicted.enqueued_at, label,
                )
            self._queue.append(task)
            self.metrics.enqueued_total += 1
            self.metrics.queue_depth = len(self._queue)
            self._condition.notify()

    def _worker_loop(self) -> None:
        while True:
            with self._condition:
                while not self._queue and not self._closing:
                    self._condition.wait(timeout=1.0)
                if not self._queue:
                    # Only reachable with _closing True and nothing left —
                    # this worker's job is done.
                    return
                task = self._queue.pop()  # rightmost = freshest (LIFO)
                self.metrics.queue_depth = len(self._queue)

            wait_seconds = time.monotonic() - task.enqueued_at
            if wait_seconds > self._staleness_seconds:
                with self._condition:
                    self.metrics.dropped_stale_total += 1
                    self.metrics.record_queue_wait(wait_seconds)
                logger.warning(
                    "SemanticAdmissionQueue: dropped STALE task (label=%s, waited=%.2fs > "
                    "staleness_seconds=%.2fs) — not executed",
                    task.label, wait_seconds, self._staleness_seconds,
                )
                continue

            with self._condition:
                self.metrics.dequeued_total += 1
                self.metrics.record_queue_wait(wait_seconds)
                self.metrics.active_count += 1
            try:
                task.run()
                with self._condition:
                    self.metrics.completed_total += 1
            except Exception:
                # Per the module docstring: _run_loop_b already catches its
                # own VLM/LLM/Verifier/DB failures internally and never lets
                # them propagate here under normal operation. Reaching this
                # branch means the task wrapper itself raised — a genuinely
                # new, unexpected bug, never silently swallowed.
                with self._condition:
                    self.metrics.failed_total += 1
                logger.exception("SemanticAdmissionQueue: task (label=%s) raised", task.label)
            finally:
                with self._condition:
                    self.metrics.active_count -= 1

    def close(self, timeout: Optional[float] = None) -> None:
        """Called once by AnalysisOrchestrator.run() after Loop A finishes
        — stops accepting new work and waits for the queue to fully drain
        (each remaining queued item still gets its normal staleness check;
        an item that's already stale by the time a worker reaches it is
        dropped exactly as it would be mid-run, never force-run just
        because the session ended) AND for any currently-ACTIVE task to
        finish (an already-started chain is never interrupted mid-flight —
        "active semantic work may finish according to current production
        policy," per Phase G). Bounded by each task's own already-bounded
        per-call timeouts (VLM/Reasoner/Verifier all have real ceiling
        timeouts) — never truly unbounded in practice."""
        with self._condition:
            self._closing = True
            self._condition.notify_all()
        for worker in self._workers:
            worker.join(timeout=timeout)

    def metrics_snapshot(self) -> dict:
        with self._condition:
            return self.metrics.snapshot()
