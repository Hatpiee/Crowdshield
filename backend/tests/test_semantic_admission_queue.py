"""Semantic Admission Control phase: unit tests for SemanticAdmissionQueue's
own bookkeeping/scheduling logic in isolation from AnalysisOrchestrator (no
real VLM/Reasoner/DB calls anywhere in this file — pure threading/queueing
behavior, matching the governing task's own "do not put real Ollama calls
in all unit tests" instruction)."""

import threading
import time

from app.pipeline.semantic_admission_queue import SemanticAdmissionQueue


def test_submit_never_blocks_caller_even_while_a_worker_is_busy():
    """Loop A must never block on semantic work — submit() itself must
    return near-instantly regardless of what state the queue/workers are
    in."""
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=10.0)
    started = threading.Event()
    release = threading.Event()

    def _slow_task():
        started.set()
        release.wait(timeout=5)

    queue.submit(run=_slow_task, trigger_timestamp_seconds=0.0, label="first")
    assert started.wait(timeout=2), "first task never started"

    start = time.monotonic()
    queue.submit(run=lambda: None, trigger_timestamp_seconds=1.0, label="second")
    elapsed = time.monotonic() - start
    assert elapsed < 0.1, f"submit() took {elapsed:.3f}s while a worker was busy — must be non-blocking"

    release.set()
    queue.close(timeout=5)


def test_second_task_queues_while_first_active_and_both_eventually_run():
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=30.0)
    started = threading.Event()
    release = threading.Event()
    ran: list[str] = []

    def _first():
        started.set()
        release.wait(timeout=5)
        ran.append("first")

    def _second():
        ran.append("second")

    queue.submit(run=_first, trigger_timestamp_seconds=0.0, label="first")
    assert started.wait(timeout=2)
    queue.submit(run=_second, trigger_timestamp_seconds=1.0, label="second")

    # Not dropped — genuinely queued (capacity=2, only 1 item pending).
    snapshot = queue.metrics_snapshot()
    assert snapshot["queue_depth"] == 1
    assert snapshot["dropped_capacity_total"] == 0
    assert snapshot["dropped_stale_total"] == 0

    release.set()
    queue.close(timeout=5)

    assert ran == ["first", "second"]
    final = queue.metrics_snapshot()
    assert final["completed_total"] == 2
    assert final["dequeued_total"] == 2


def test_queue_overflow_evicts_oldest_queued_task_not_the_newest():
    """max_queue_depth=1: three total submissions while a worker is busy
    (1 active + 1 queued already at capacity) — the THIRD submission must
    evict the SECOND (oldest still-queued), not itself be rejected, and
    not evict the currently-ACTIVE first task."""
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=1, staleness_seconds=30.0)
    started = threading.Event()
    release = threading.Event()
    ran: list[str] = []

    queue.submit(run=lambda: (started.set(), release.wait(timeout=5), ran.append("first")), trigger_timestamp_seconds=0.0, label="first")
    assert started.wait(timeout=2)

    queue.submit(run=lambda: ran.append("second"), trigger_timestamp_seconds=1.0, label="second")
    queue.submit(run=lambda: ran.append("third"), trigger_timestamp_seconds=2.0, label="third")

    snapshot = queue.metrics_snapshot()
    assert snapshot["dropped_capacity_total"] == 1, "the stale queued 'second' task must have been evicted"
    assert snapshot["queue_depth"] == 1

    release.set()
    queue.close(timeout=5)

    assert "first" in ran
    assert "third" in ran
    assert "second" not in ran, "'second' was evicted by capacity — must never execute"


def test_stale_queued_task_is_dropped_not_executed():
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=0.05)
    started = threading.Event()
    release = threading.Event()
    executed = threading.Event()

    queue.submit(run=lambda: (started.set(), release.wait(timeout=5)), trigger_timestamp_seconds=0.0, label="first")
    assert started.wait(timeout=2)

    queue.submit(run=lambda: executed.set(), trigger_timestamp_seconds=1.0, label="stale-candidate")
    time.sleep(0.2)  # comfortably exceed staleness_seconds=0.05 while it waits behind "first"

    release.set()
    queue.close(timeout=5)

    assert not executed.is_set(), "a task that waited past staleness_seconds must never execute"
    snapshot = queue.metrics_snapshot()
    assert snapshot["dropped_stale_total"] == 1
    assert snapshot["completed_total"] == 1  # only "first"


def test_close_joins_all_worker_threads_no_orphan():
    queue = SemanticAdmissionQueue(max_concurrent=2, max_queue_depth=2, staleness_seconds=10.0)
    assert all(w.is_alive() for w in queue._workers)

    queue.close(timeout=5)

    assert all(not w.is_alive() for w in queue._workers), "close() must join every worker thread — none orphaned"


def test_close_waits_for_active_task_to_finish_not_interrupted():
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=10.0)
    started = threading.Event()
    finished = threading.Event()

    def _task():
        started.set()
        time.sleep(0.2)
        finished.set()

    queue.submit(run=_task, trigger_timestamp_seconds=0.0, label="active")
    assert started.wait(timeout=2)

    queue.close(timeout=5)  # must block until the active task genuinely finishes

    assert finished.is_set(), "close() returned before the already-active task finished"


def test_submit_after_close_is_rejected_not_silently_ignored():
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=10.0)
    queue.close(timeout=5)

    executed = threading.Event()
    queue.submit(run=lambda: executed.set(), trigger_timestamp_seconds=5.0, label="late")
    time.sleep(0.1)

    assert not executed.is_set()
    snapshot = queue.metrics_snapshot()
    assert snapshot["dropped_stale_total"] == 1


def test_metrics_snapshot_never_fabricates_queue_wait_when_nothing_ran():
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=10.0)
    snapshot = queue.metrics_snapshot()
    assert snapshot["queue_wait_seconds_mean"] is None
    assert snapshot["queue_wait_seconds_max"] is None
    queue.close(timeout=5)


def test_failed_task_is_counted_and_does_not_crash_the_worker():
    queue = SemanticAdmissionQueue(max_concurrent=1, max_queue_depth=2, staleness_seconds=10.0)

    def _boom():
        raise RuntimeError("synthetic task failure")

    ran_after = threading.Event()
    queue.submit(run=_boom, trigger_timestamp_seconds=0.0, label="boom")
    queue.submit(run=lambda: ran_after.set(), trigger_timestamp_seconds=1.0, label="after")
    queue.close(timeout=5)

    assert ran_after.is_set(), "a failed task must not take the worker down — later work must still run"
    snapshot = queue.metrics_snapshot()
    assert snapshot["failed_total"] == 1
    assert snapshot["completed_total"] == 1
