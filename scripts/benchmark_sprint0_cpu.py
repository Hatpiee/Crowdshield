"""Sprint-0 CPU / Concurrency / Ollama Load Test — MEASUREMENT ONLY.

This script does not optimize, tune, or alter anything. It measures the
REAL, UNMODIFIED CrowdShield pipeline (real YOLO/ByteTrack/DIS-optical-flow/
CrowdMetricsEngine/TriggerEngine for Loop A; real MiniCPM-V/Qwen3-8B via
Ollama for Loop B) under a small set of real workloads and reports the raw
numbers. No `sleep()`/mocked latency is ever used for a semantic call — see
each scenario's own docstring for exactly which real components it exercises.

============================================================
HOW THIS AVOIDS TOUCHING PRODUCTION CODE
============================================================
Two different techniques are used, chosen per scenario to stay maximally
faithful to production behavior while never editing
`analysis_orchestrator.py` (or any other pipeline file):

1. Scenarios A/B/C (`run_loop_a_baseline`, `run_loop_a_plus_vlm`,
   `run_loop_a_plus_vlm_plus_reasoner`) use a benchmark-only Loop A/B DRIVER
   (`_BenchmarkDriver` below) that composes the exact same real component
   classes AnalysisOrchestrator itself composes (same construction, same
   call sequence, same real `MAX_CONCURRENT_SEMANTIC_ANALYSES` semaphore
   drop behavior) — this is the SAME "compose real pipeline stages
   directly, no orchestrator" pattern this repo's own `scripts/preview_*.py`
   and `scripts/calibrate_acute_hazard_false_positives.py` already
   establish, just instrumented for timing. It exists ONLY so a scenario
   can stop the real chain early (VLM-only, or VLM+Reasoner without
   Verifier) — something the real orchestrator's `_run_loop_b` cannot do
   without editing it, which this phase forbids.
2. Scenario D (`run_full_chain_via_orchestrator`) uses the REAL,
   byte-for-byte UNMODIFIED `AnalysisOrchestrator.run()` — the actual
   production entry point (same pattern as `scripts/validate_acute_event_
   video.py --full-chain`). Loop-A frame latency is captured by TEMPORARILY
   monkeypatching `TriggerEngine.evaluate` (called exactly once near the
   end of each Loop-A frame iteration) to record a timestamp on entry —
   the inter-call delta is this benchmark's proxy for "time to process one
   full frame" (detection + tracking + optical flow + crowd intelligence +
   risk state, all of which already completed before `evaluate()` is
   reached). The monkeypatch is restored immediately after the run,
   regardless of outcome. `analysis_orchestrator.py` itself is never
   edited.

Usage:
  python scripts/benchmark_sprint0_cpu.py <scenario> [options]
  scenarios: baseline | vlm | reasoner | full-chain | sweep | multi-session | warm-cold | all
"""

import argparse
import json
import platform
import shutil
import statistics
import subprocess
import sys
import threading
import time
import uuid
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import psutil  # noqa: E402

from app.core.config import REPO_ROOT, settings  # noqa: E402
from app.core.database import SessionLocal  # noqa: E402
from app.models.analysis_session import AnalysisSession  # noqa: E402
from app.models.user import User  # noqa: E402
from app.models.video import VideoAsset  # noqa: E402
from app.pipeline.acute_hazard_detector import AcuteHazardDetector  # noqa: E402
from app.pipeline.analysis_orchestrator import AnalysisOrchestrator  # noqa: E402
from app.pipeline.bytetrack_adapter import ByteTrackAdapter  # noqa: E402
from app.pipeline.crowd_grid import CrowdGrid  # noqa: E402
from app.pipeline.crowd_metrics import CrowdMetricsEngine  # noqa: E402
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter  # noqa: E402
from app.pipeline.evidence_builder import EvidenceBuilder  # noqa: E402
from app.pipeline.minicpm_vlm import MiniCPMVisionModel, VLMUnavailableError, VLMResponseValidationError  # noqa: E402
from app.pipeline.mp4_frame_source import MP4FrameSource  # noqa: E402
from app.pipeline.reasoner import LLMResponseValidationError, LLMUnavailableError, Reasoner  # noqa: E402
from app.pipeline.risk_score import compute_risk_grid  # noqa: E402
from app.pipeline.risk_state import RiskStateMachine  # noqa: E402
from app.pipeline.roi_selection import select_roi  # noqa: E402
from app.pipeline.trigger_engine import TriggerDecision, TriggerEngine, TriggerType  # noqa: E402
from app.pipeline.verifier import LLMVerificationResponseValidationError, LLMVerificationUnavailableError, Verifier  # noqa: E402
from app.pipeline.vision_observation import CompactCrowdMetricsSummary, VisionInput  # noqa: E402
from app.pipeline.yolo_detector import YOLO11nDetector  # noqa: E402
from app.services import session_service  # noqa: E402

_PEOPLE_CLIP_VIDEO_ID = uuid.UUID("7578ea39-4a8a-40d3-a1e6-75bf0dc75e63")
_RESOURCE_SAMPLE_INTERVAL_SECONDS = 0.5


# ============================================================
# Machine metadata (Step 3)
# ============================================================

def _ollama_version() -> Optional[str]:
    try:
        result = subprocess.run(["ollama", "--version"], capture_output=True, text=True, timeout=5)
        return result.stdout.strip() or result.stderr.strip()
    except Exception:
        return None


def collect_machine_metadata() -> dict:
    memory = psutil.virtual_memory()
    return {
        "timestamp_utc": datetime.now(timezone.utc).isoformat(),
        "os": platform.platform(),
        "python_version": platform.python_version(),
        "cpu_model": platform.processor() or None,
        "physical_cores": psutil.cpu_count(logical=False),
        "logical_cores": psutil.cpu_count(logical=True),
        "total_ram_gb": round(memory.total / (1024 ** 3), 2),
        "ollama_version": _ollama_version(),
        "vlm_model": settings.VLM_MODEL,
        "llm_model": settings.LLM_MODEL,
        "max_concurrent_semantic_analyses": settings.MAX_CONCURRENT_SEMANTIC_ANALYSES,
        "vlm_request_timeout_seconds": settings.VLM_REQUEST_TIMEOUT_SECONDS,
        "llm_request_timeout_seconds": settings.LLM_REQUEST_TIMEOUT_SECONDS,
        "verifier_request_timeout_seconds": settings.VERIFIER_REQUEST_TIMEOUT_SECONDS,
        "acute_hazard_cooldown_seconds": settings.ACUTE_HAZARD_COOLDOWN_SECONDS,
        "ollama_num_thread": settings.OLLAMA_NUM_THREAD,
        "dis_preset": settings.DIS_PRESET,
        "semantic_queue_max_depth": settings.SEMANTIC_QUEUE_MAX_DEPTH,
        "semantic_queue_staleness_seconds": settings.SEMANTIC_QUEUE_STALENESS_SECONDS,
    }


def video_metadata(video_path: Path) -> dict:
    import hashlib

    hasher = hashlib.sha256()
    with open(video_path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    with MP4FrameSource(video_path, frame_step=1) as source:
        meta = source.get_metadata()
    return {
        "path": str(video_path),
        "sha256": hasher.hexdigest(),
        "duration_seconds": meta.duration_seconds,
        "fps": meta.fps,
        "frame_count": meta.frame_count,
        "width": meta.width,
        "height": meta.height,
    }


# ============================================================
# Latency statistics (Step 4, Step 20)
# ============================================================

def percentile(values: list[float], pct: float) -> Optional[float]:
    if not values:
        return None
    ordered = sorted(values)
    k = (len(ordered) - 1) * (pct / 100.0)
    f, c = int(k), min(int(k) + 1, len(ordered) - 1)
    if f == c:
        return ordered[f]
    return ordered[f] + (ordered[c] - ordered[f]) * (k - f)


def compute_degradation_percent(baseline_metric: Optional[float], contended_metric: Optional[float]) -> Optional[float]:
    """Step 5's exact formula: ((baseline - contended) / baseline) * 100.
    Intended for THROUGHPUT-style metrics (e.g. effective FPS) where a
    SMALLER contended value means WORSE (positive % = degradation). For a
    LATENCY-style metric, where a LARGER contended value means worse, pass
    metrics in already-inverted form at the call site (documented per
    Step 5's "for latency use a clearly documented inverse comparison"
    instruction) rather than silently reinterpreting this same formula.
    Returns None (never a fabricated number) when either input is missing
    or baseline is zero."""
    if baseline_metric is None or contended_metric is None or baseline_metric == 0:
        return None
    return ((baseline_metric - contended_metric) / baseline_metric) * 100.0


def latency_summary(values: list[float]) -> dict:
    if not values:
        return {"count": 0, "mean": None, "p50": None, "p95": None, "p99": None, "max": None, "min": None}
    return {
        "count": len(values),
        "mean": statistics.fmean(values),
        "p50": percentile(values, 50),
        "p95": percentile(values, 95),
        "p99": percentile(values, 99),
        "max": max(values),
        "min": min(values),
    }


# ============================================================
# Resource sampling (Step 11 CPU attribution, Step 12 memory)
# ============================================================

class ResourceSampler:
    """Samples THIS process's own CPU%/RSS plus every `ollama` OS process's
    CPU%/RSS on a fixed interval, on its own background thread — a
    benchmark-only observer, no new monitoring infrastructure (reuses
    `psutil`, already a real backend dependency)."""

    def __init__(self, interval_seconds: float = _RESOURCE_SAMPLE_INTERVAL_SECONDS) -> None:
        self._interval = interval_seconds
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        self.backend_cpu_samples: list[float] = []
        self.backend_rss_samples: list[float] = []
        self.ollama_cpu_samples: list[float] = []
        self.ollama_rss_samples: list[float] = []
        self._self_proc = psutil.Process()

    def _ollama_processes(self) -> list[psutil.Process]:
        # REAL BUG CAUGHT WHILE RUNNING THIS BENCHMARK FOR REAL (see
        # DECISIONS.md): matching only "ollama" in the process name misses
        # the actual inference worker entirely. On this real machine,
        # `ollama serve` (the lightweight API daemon, ~1s cumulative CPU
        # over a full VLM scenario) spawns a SEPARATE `llama-server.exe`
        # process that does the real CPU-bound generation work (hundreds of
        # CPU-seconds) — confirmed via `Get-Process | Where-Object
        # ProcessName -like "*llama*"` during a real run. Matching both
        # names is what actually attributes Ollama's real CPU cost.
        procs = []
        for proc in psutil.process_iter(["name"]):
            try:
                name = (proc.info["name"] or "").lower()
                if "ollama" in name or "llama-server" in name or "llama_server" in name:
                    procs.append(proc)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        return procs

    def _run(self) -> None:
        self._self_proc.cpu_percent(interval=None)  # prime the internal counter
        ollama_procs = self._ollama_processes()
        for proc in ollama_procs:
            try:
                proc.cpu_percent(interval=None)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
        while not self._stop.wait(self._interval):
            try:
                self.backend_cpu_samples.append(self._self_proc.cpu_percent(interval=None))
                self.backend_rss_samples.append(self._self_proc.memory_info().rss / (1024 ** 2))
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                pass
            for proc in self._ollama_processes():
                try:
                    self.ollama_cpu_samples.append(proc.cpu_percent(interval=None))
                    self.ollama_rss_samples.append(proc.memory_info().rss / (1024 ** 2))
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=5.0)
        return {
            "backend_cpu_percent_mean": statistics.fmean(self.backend_cpu_samples) if self.backend_cpu_samples else None,
            "backend_cpu_percent_max": max(self.backend_cpu_samples) if self.backend_cpu_samples else None,
            "backend_rss_mb_mean": statistics.fmean(self.backend_rss_samples) if self.backend_rss_samples else None,
            "backend_rss_mb_peak": max(self.backend_rss_samples) if self.backend_rss_samples else None,
            "ollama_cpu_percent_mean": statistics.fmean(self.ollama_cpu_samples) if self.ollama_cpu_samples else None,
            "ollama_cpu_percent_max": max(self.ollama_cpu_samples) if self.ollama_cpu_samples else None,
            "ollama_rss_mb_mean": statistics.fmean(self.ollama_rss_samples) if self.ollama_rss_samples else None,
            "ollama_rss_mb_peak": max(self.ollama_rss_samples) if self.ollama_rss_samples else None,
            "sample_count": len(self.backend_cpu_samples),
        }


# ============================================================
# Failure accounting (Step 14) — never collapse a timeout into zero latency
# ============================================================

@dataclass
class FailureRecord:
    stage: str
    exception_type: str
    elapsed_seconds: float
    retry_count: int = 0


@dataclass
class FailureCounter:
    attempted: int = 0
    successful: int = 0
    failed: int = 0
    timed_out: int = 0
    dropped: int = 0
    retried: int = 0
    records: list[FailureRecord] = field(default_factory=list)

    def record_success(self) -> None:
        self.attempted += 1
        self.successful += 1

    def record_failure(self, stage: str, exc: Exception, elapsed_seconds: float, timed_out: bool = False, retry_count: int = 0) -> None:
        self.attempted += 1
        self.failed += 1
        if timed_out:
            self.timed_out += 1
        if retry_count > 0:
            self.retried += 1
        self.records.append(FailureRecord(stage=stage, exception_type=type(exc).__name__, elapsed_seconds=elapsed_seconds, retry_count=retry_count))

    def record_dropped(self) -> None:
        self.dropped += 1


# ============================================================
# Semantic call records (Step 5-7)
# ============================================================

@dataclass
class SemanticCallRecord:
    stage: str  # VLM | REASONER | VERIFIER
    trigger_timestamp_seconds: float
    latency_seconds: float
    success: bool
    timed_out: bool = False
    error: Optional[str] = None


# ============================================================
# Loop-A frame-latency instrumentation for the REAL, unmodified orchestrator
# (Scenario D) — see module docstring, technique 2.
# ============================================================

@contextmanager
def _instrument_trigger_engine_for_frame_latency(latencies: list[float]):
    original_evaluate = TriggerEngine.evaluate
    last_ts = {"t": None}

    def _wrapped(self, *args, **kwargs):
        now = time.perf_counter()
        if last_ts["t"] is not None:
            latencies.append(now - last_ts["t"])
        last_ts["t"] = now
        return original_evaluate(self, *args, **kwargs)

    TriggerEngine.evaluate = _wrapped
    try:
        yield
    finally:
        TriggerEngine.evaluate = original_evaluate


# ============================================================
# Benchmark-only Loop A/B driver (Scenarios A/B/C) — see module docstring,
# technique 1. Mirrors AnalysisOrchestrator._run_loop_a/_maybe_spawn_loop_b's
# REAL composition and semaphore semantics, instrumented for timing.
# ============================================================

class _BenchmarkDriver:
    def __init__(self, semantic_mode: str) -> None:
        # semantic_mode: "none" | "vlm" | "vlm_reasoner"
        self.semantic_mode = semantic_mode
        self.frame_latencies: list[float] = []
        self.semantic_calls: list[SemanticCallRecord] = []
        self.failures = FailureCounter()
        self.trigger_attempts = 0
        self.trigger_dropped = 0
        self._semaphore = threading.Semaphore(settings.MAX_CONCURRENT_SEMANTIC_ANALYSES)
        self._lock = threading.Lock()
        self._threads: list[threading.Thread] = []

    def _run_semantic_chain(self, frame, roi_bbox, compact_metrics, trigger_reason: str, crowd_metrics, risk_result, trigger_decision, db_session_id: Optional[uuid.UUID]) -> None:
        try:
            vision_model = MiniCPMVisionModel()
            vision_input = VisionInput(
                representative_frame=frame, roi_crop_bbox=roi_bbox,
                compact_metrics=compact_metrics, trigger_reason=trigger_reason,
            )
            start = time.perf_counter()
            try:
                vision_result = vision_model.analyze(vision_input)
                elapsed = time.perf_counter() - start
                with self._lock:
                    self.semantic_calls.append(SemanticCallRecord("VLM", frame.timestamp_seconds, elapsed, True))
                    self.failures.record_success()
            except (VLMUnavailableError, VLMResponseValidationError) as exc:
                elapsed = time.perf_counter() - start
                timed_out = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
                with self._lock:
                    self.semantic_calls.append(SemanticCallRecord("VLM", frame.timestamp_seconds, elapsed, False, timed_out, str(exc)))
                    self.failures.record_failure("VLM", exc, elapsed, timed_out=timed_out)
                return

            if self.semantic_mode == "vlm":
                return

            if db_session_id is None:
                return
            db = SessionLocal()
            try:
                builder = EvidenceBuilder()
                package_result = builder.build(
                    db=db, session_id=db_session_id, frame=frame, crowd_metrics=crowd_metrics,
                    risk_state_result=risk_result, trigger_decision=trigger_decision, roi_bbox=roi_bbox,
                    vision_result=vision_result, vlm_call_succeeded=True,
                    predictive_projection=crowd_metrics.predictive_projection,
                )
                reasoner = Reasoner()
                start = time.perf_counter()
                try:
                    decision_result = reasoner.reason(package_result)
                    elapsed = time.perf_counter() - start
                    with self._lock:
                        self.semantic_calls.append(SemanticCallRecord("REASONER", frame.timestamp_seconds, elapsed, True))
                        self.failures.record_success()
                except (LLMUnavailableError, LLMResponseValidationError) as exc:
                    elapsed = time.perf_counter() - start
                    timed_out = "timeout" in str(exc).lower() or "timed out" in str(exc).lower()
                    with self._lock:
                        self.semantic_calls.append(SemanticCallRecord("REASONER", frame.timestamp_seconds, elapsed, False, timed_out, str(exc)))
                        self.failures.record_failure("REASONER", exc, elapsed, timed_out=timed_out)
            finally:
                db.close()
        finally:
            self._semaphore.release()

    def maybe_spawn(self, frame, crowd_metrics, risk_result, trigger_decision: TriggerDecision, crowd_grid: CrowdGrid, frame_width: int, frame_height: int, db_session_id: Optional[uuid.UUID]) -> None:
        if trigger_decision.trigger_type == TriggerType.NONE:
            return
        self.trigger_attempts += 1
        if self.semantic_mode == "none":
            return
        if not self._semaphore.acquire(blocking=False):
            self.trigger_dropped += 1
            with self._lock:
                self.failures.record_dropped()
            return

        risk_grid = compute_risk_grid(crowd_metrics.core.pressure, crowd_metrics.congestion, crowd_metrics.bottleneck, crowd_metrics.reverse_flow)
        roi_bbox = select_roi(risk_grid, crowd_grid, frame_width, frame_height)
        compact_metrics = CompactCrowdMetricsSummary(
            risk_score=risk_result.risk_score, risk_state=risk_result.state,
            max_density=float(crowd_metrics.core.density.grid.max()), max_pressure=crowd_metrics.core.pressure.max_pressure,
            pressure_units_disclaimer=crowd_metrics.core.pressure.units_disclaimer,
            congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
            reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
            bottleneck_signal_present=crowd_metrics.bottleneck is not None,
            density_confidence=crowd_metrics.core.density.estimation_confidence,
        )
        thread = threading.Thread(
            target=self._run_semantic_chain,
            args=(frame, roi_bbox, compact_metrics, trigger_decision.reason, crowd_metrics, risk_result, trigger_decision, db_session_id),
            daemon=False,
        )
        self._threads.append(thread)
        thread.start()

    def join_all(self) -> None:
        for thread in self._threads:
            thread.join()


def _run_benchmark_driver(
    video_path: Path, semantic_mode: str, operator_trigger_interval_seconds: Optional[float] = None,
    db_session_id: Optional[uuid.UUID] = None, stage_timings: Optional[dict[str, list[float]]] = None,
) -> dict:
    """Real Loop A (every frame) + optional real Loop B (VLM-only or
    VLM+Reasoner), using genuinely real triggers (RISK/FALLBACK/ACUTE_HAZARD
    exactly as production computes them) OR, if `operator_trigger_interval_
    seconds` is set, ALSO a real, unmodified OPERATOR trigger injected at a
    controlled cadence (Decision #8's existing, already-designed-for-this
    trigger source — used here to construct a defensible, non-arbitrary
    LOW/MODERATE/HIGH semantic-frequency sweep without inventing a new
    trigger mechanism or touching ACUTE_HAZARD's own thresholds).

    Phase C (Loop-A Tail-Latency Investigation): if `stage_timings` is
    passed (an empty dict the caller owns), each real Loop-A stage's own
    wall-clock cost is recorded into it separately (keys: "detection",
    "tracking", "optical_flow", "crowd_metrics", "acute_hazard",
    "risk_state", "trigger_evaluation", "semantic_admission_submit") —
    benchmark-only instrumentation, never added to production code. Since
    each stage is timed via a plain `time.perf_counter()` pair placed
    directly around the REAL call (not a monkeypatch), a stall caused by
    the OS delaying this thread WHILE inside that call (true CPU/scheduling
    contention) is correctly attributed to that stage; a stall between
    stages would instead show up as a gap between "frame_latency" (the
    pre-existing whole-frame total, unchanged) and the sum of the
    per-stage times below."""
    driver = _BenchmarkDriver(semantic_mode)

    def _time_stage(key: str, fn, *args):
        if stage_timings is None:
            return fn(*args)
        start = time.perf_counter()
        result = fn(*args)
        stage_timings.setdefault(key, []).append(time.perf_counter() - start)
        return result

    with MP4FrameSource(video_path, frame_step=1) as source:
        metadata = source.get_metadata()
        fps, width, height = metadata.fps, metadata.width, metadata.height

        detector = YOLO11nDetector()
        tracker = ByteTrackAdapter(fps=fps)
        optical_flow = DISOpticalFlowAdapter()
        engine = CrowdMetricsEngine(frame_width=width, frame_height=height)
        risk_machine = RiskStateMachine()
        trigger_engine = TriggerEngine()
        crowd_grid = CrowdGrid.from_frame_dimensions(width, height)
        acute_detector = AcuteHazardDetector(crowd_grid)

        prev_frame = None
        last_operator_trigger_ts = 0.0
        wall_clock_start = time.perf_counter()
        for frame in source.frames():
            detection_result = _time_stage("detection", detector.detect, frame)
            tracking_result = _time_stage("tracking", tracker.update, detection_result)

            if prev_frame is not None:
                frame_start = time.perf_counter()
                motion_result = _time_stage("optical_flow", optical_flow.compute, prev_frame, frame)
                elapsed_seconds = frame.timestamp_seconds - prev_frame.timestamp_seconds
                crowd_metrics = _time_stage("crowd_metrics", engine.update, tracking_result, motion_result, elapsed_seconds)
                acute_signal = _time_stage(
                    "acute_hazard", acute_detector.update,
                    motion_result, crowd_metrics.core.flow, detection_result, prev_frame, frame,
                )
                risk_result = _time_stage("risk_state", risk_machine.update, crowd_metrics)

                operator_requested = False
                if operator_trigger_interval_seconds is not None and (frame.timestamp_seconds - last_operator_trigger_ts) >= operator_trigger_interval_seconds:
                    operator_requested = True
                    last_operator_trigger_ts = frame.timestamp_seconds

                trigger_decision = _time_stage(
                    "trigger_evaluation", trigger_engine.evaluate,
                    crowd_metrics, risk_result, operator_requested, acute_signal,
                )
                driver.frame_latencies.append(time.perf_counter() - frame_start)
                _time_stage(
                    "semantic_admission_submit", driver.maybe_spawn,
                    frame, crowd_metrics, risk_result, trigger_decision, crowd_grid, width, height, db_session_id,
                )

            prev_frame = frame

        loop_a_wall_seconds = time.perf_counter() - wall_clock_start
        driver.join_all()
        total_wall_seconds = time.perf_counter() - wall_clock_start

    return {
        "frame_count": metadata.frame_count,
        "video_duration_seconds": metadata.duration_seconds,
        "loop_a_wall_seconds": loop_a_wall_seconds,
        "loop_a_effective_fps": metadata.frame_count / loop_a_wall_seconds if loop_a_wall_seconds > 0 else None,
        "total_wall_seconds_including_semantic_join": total_wall_seconds,
        "frame_latency": latency_summary(driver.frame_latencies),
        "trigger_attempts": driver.trigger_attempts,
        "trigger_dropped_by_semaphore": driver.trigger_dropped,
        "semantic_calls": [asdict(c) for c in driver.semantic_calls],
        "semantic_by_stage": {
            stage: latency_summary([c.latency_seconds for c in driver.semantic_calls if c.stage == stage and c.success])
            for stage in ("VLM", "REASONER")
        },
        "failures": asdict(driver.failures),
    }


# ============================================================
# Scenario runners
# ============================================================

def _resolve_benchmark_video() -> Path:
    return REPO_ROOT / settings.VIDEO_STORAGE_PATH / "779fa633-d5f6-4e49-9045-9fd04e691ddb.mp4"


def _write_result(output_dir: Path, scenario: str, result: dict) -> Path:
    output_dir.mkdir(parents=True, exist_ok=True)
    path = output_dir / f"{scenario}.json"
    path.write_text(json.dumps(result, indent=2, default=str), encoding="utf-8")
    return path


def run_loop_a_baseline(video_path: Path, output_dir: Path) -> dict:
    """BASELINE_LOOP_A_ONLY — real Loop A, zero semantic calls (Loop B
    entirely absent from this scenario, not disabled via any production
    flag)."""
    sampler = ResourceSampler()
    sampler.start()
    driver_result = _run_benchmark_driver(video_path, semantic_mode="none")
    resources = sampler.stop()

    result = {
        "scenario": "BASELINE_LOOP_A_ONLY", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "loop_a": driver_result, "resources": resources,
    }
    _write_result(output_dir, "baseline_loop_a_only", result)
    return result


def run_loop_a_plus_vlm(video_path: Path, output_dir: Path) -> dict:
    sampler = ResourceSampler()
    sampler.start()
    driver_result = _run_benchmark_driver(video_path, semantic_mode="vlm")
    resources = sampler.stop()

    result = {
        "scenario": "LOOP_A_PLUS_VLM", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "loop_a": driver_result, "resources": resources,
    }
    _write_result(output_dir, "loop_a_plus_vlm", result)
    return result


def run_loop_a_plus_vlm_plus_reasoner(video_path: Path, output_dir: Path) -> dict:
    db = SessionLocal()
    try:
        user = db.query(User).first()
        video = db.get(VideoAsset, _PEOPLE_CLIP_VIDEO_ID)
        session = session_service.create_session(db, video.id, user.id)
        session = session_service.start_session(db, session)
        db_session_id = session.id
    finally:
        db.close()

    sampler = ResourceSampler()
    sampler.start()
    driver_result = _run_benchmark_driver(video_path, semantic_mode="vlm_reasoner", db_session_id=db_session_id)
    resources = sampler.stop()

    result = {
        "scenario": "LOOP_A_PLUS_VLM_PLUS_REASONER", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "loop_a": driver_result, "resources": resources,
        "benchmark_session_id": str(db_session_id),
    }
    _write_result(output_dir, "loop_a_plus_vlm_plus_reasoner", result)
    return result


def run_full_chain_via_orchestrator(video_path: Path, output_dir: Path, sample_id: str = "full_chain") -> dict:
    """Full semantic chain, via the REAL, UNMODIFIED AnalysisOrchestrator —
    see module docstring, technique 2. If no trigger reaches outcome=
    INCIDENT during this real run, the Verifier is genuinely never invoked
    — reported honestly, never forced."""
    db = SessionLocal()
    try:
        user = db.query(User).first()
        storage_filename = f"{uuid.uuid4()}.mp4"
        storage_dir = REPO_ROOT / settings.VIDEO_STORAGE_PATH
        storage_path = storage_dir / storage_filename
        shutil.copyfile(video_path, storage_path)
        with MP4FrameSource(storage_path, frame_step=1) as source:
            meta = source.get_metadata()
        video = VideoAsset(
            original_filename=f"BENCHMARK_{sample_id}.mp4", storage_filename=storage_filename,
            file_size_bytes=storage_path.stat().st_size, mime_type="video/mp4", uploaded_by=user.id,
            fps=meta.fps, width=meta.width, height=meta.height, duration_seconds=meta.duration_seconds, frame_count=meta.frame_count,
        )
        db.add(video)
        db.commit()
        db.refresh(video)
        session = session_service.create_session(db, video.id, user.id)
        session = session_service.start_session(db, session)
        session_id = session.id
    finally:
        db.close()

    frame_latencies: list[float] = []
    sampler = ResourceSampler()
    sampler.start()
    wall_start = time.perf_counter()
    with _instrument_trigger_engine_for_frame_latency(frame_latencies):
        orchestrator = AnalysisOrchestrator(session_id)
        orchestrator.run()
    total_wall_seconds = time.perf_counter() - wall_start
    resources = sampler.stop()

    db = SessionLocal()
    try:
        from app.models.decision import DecisionResultRow
        from app.models.evidence import EvidencePackage
        from app.models.incident import Incident
        from app.models.verification import VerificationResultRow

        packages = db.query(EvidencePackage).filter(EvidencePackage.session_id == session_id).all()
        decisions = db.query(DecisionResultRow).join(EvidencePackage).filter(EvidencePackage.session_id == session_id).all()
        incidents = db.query(Incident).filter(Incident.session_id == session_id).all()
        verifications = db.query(VerificationResultRow).filter(
            VerificationResultRow.decision_id.in_([d.id for d in decisions])
        ).all() if decisions else []
        session_row = db.get(AnalysisSession, session_id)
        final_status = session_row.status.value if session_row else None
    finally:
        db.close()

    result = {
        "scenario": "FULL_SEMANTIC_CHAIN_VIA_ORCHESTRATOR", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "resources": resources,
        "session_id": str(session_id), "final_session_status": final_status,
        "total_wall_seconds": total_wall_seconds,
        "frame_latency": latency_summary(frame_latencies),
        "loop_a_effective_fps": meta.frame_count / total_wall_seconds if total_wall_seconds > 0 else None,
        "evidence_package_count": len(packages),
        "decision_count": len(decisions),
        "decision_outcomes": [d.outcome.value for d in decisions],
        "incident_count": len(incidents),
        "verifier_invocation_count": len(verifications),
        "verifier_invoked": len(verifications) > 0,
    }
    _write_result(output_dir, sample_id, result)
    return result


def run_trigger_frequency_sweep(video_path: Path, output_dir: Path) -> dict:
    """Step 8 — LOW/MODERATE/HIGH semantic-request frequency, using the
    REAL, existing, unmodified `operator_requested` trigger path
    (TriggerEngine's own Decision #8) injected at a controlled cadence —
    NOT a new trigger mechanism, NOT a change to ACUTE_HAZARD's own
    thresholds."""
    levels = {"LOW": 15.0, "MODERATE": 7.0, "HIGH": 3.0}
    results = {}
    db = SessionLocal()
    try:
        user = db.query(User).first()
        video = db.get(VideoAsset, _PEOPLE_CLIP_VIDEO_ID)
        session = session_service.create_session(db, video.id, user.id)
        session = session_service.start_session(db, session)
        db_session_id = session.id
    finally:
        db.close()

    for level, interval in levels.items():
        sampler = ResourceSampler()
        sampler.start()
        driver_result = _run_benchmark_driver(video_path, semantic_mode="vlm_reasoner", operator_trigger_interval_seconds=interval, db_session_id=db_session_id)
        resources = sampler.stop()
        results[level] = {"trigger_interval_seconds": interval, "loop_a": driver_result, "resources": resources}

    result = {
        "scenario": "TRIGGER_FREQUENCY_SWEEP", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "levels": results, "benchmark_session_id": str(db_session_id),
    }
    _write_result(output_dir, "trigger_frequency_sweep", result)
    return result


def run_multi_session(video_path: Path, output_dir: Path, session_count: int) -> dict:
    """Step 9 — N concurrent REAL AnalysisOrchestrator.run() calls, each on
    its OWN thread with its OWN session-scoped semaphore (matching this
    project's own Decision B — the semaphore is NOT global). Measures
    whether real Ollama/CPU contention across independently-running
    sessions degrades sustainability."""
    session_ids: list[uuid.UUID] = []
    db = SessionLocal()
    try:
        user = db.query(User).first()
        for i in range(session_count):
            storage_filename = f"{uuid.uuid4()}.mp4"
            storage_dir = REPO_ROOT / settings.VIDEO_STORAGE_PATH
            storage_path = storage_dir / storage_filename
            shutil.copyfile(video_path, storage_path)
            with MP4FrameSource(storage_path, frame_step=1) as source:
                meta = source.get_metadata()
            video = VideoAsset(
                original_filename=f"BENCHMARK_multisession_{i}.mp4", storage_filename=storage_filename,
                file_size_bytes=storage_path.stat().st_size, mime_type="video/mp4", uploaded_by=user.id,
                fps=meta.fps, width=meta.width, height=meta.height, duration_seconds=meta.duration_seconds, frame_count=meta.frame_count,
            )
            db.add(video)
            db.commit()
            db.refresh(video)
            session = session_service.create_session(db, video.id, user.id)
            session = session_service.start_session(db, session)
            session_ids.append(session.id)
    finally:
        db.close()

    sampler = ResourceSampler()
    sampler.start()
    wall_start = time.perf_counter()
    threads = []
    for sid in session_ids:
        orchestrator = AnalysisOrchestrator(sid)
        t = threading.Thread(target=orchestrator.run, daemon=False)
        threads.append(t)
        t.start()
    for t in threads:
        t.join()
    total_wall_seconds = time.perf_counter() - wall_start
    resources = sampler.stop()

    db = SessionLocal()
    try:
        from app.models.decision import DecisionResultRow
        from app.models.evidence import EvidencePackage

        per_session = []
        for sid in session_ids:
            packages = db.query(EvidencePackage).filter(EvidencePackage.session_id == sid).count()
            decisions = db.query(DecisionResultRow).join(EvidencePackage).filter(EvidencePackage.session_id == sid).count()
            session_row = db.get(AnalysisSession, sid)
            per_session.append({"session_id": str(sid), "final_status": session_row.status.value if session_row else None, "evidence_packages": packages, "decisions": decisions})
    finally:
        db.close()

    result = {
        "scenario": f"MULTI_SESSION_{session_count}", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "resources": resources,
        "session_count": session_count, "total_wall_seconds": total_wall_seconds, "per_session": per_session,
    }
    _write_result(output_dir, f"multi_session_{session_count}", result)
    return result


def run_warm_cold_investigation(output_dir: Path) -> dict:
    """Step 10 — uses the REAL, supported `ollama.Client.chat(..., keep_
    alive=...)` parameter (verified present in the installed ollama Python
    client's own signature before use, per this phase's "do not invent
    unsupported parameters" instruction) to force a genuine unload
    (`keep_alive=0`) and then measure the next call's real cold-start
    latency, vs. an immediate second (warm) call."""
    import ollama

    client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=300.0)

    def _measure(model: str, messages: list[dict], keep_alive) -> dict:
        start = time.perf_counter()
        response = client.chat(model=model, messages=messages, keep_alive=keep_alive, options={"num_predict": 20})
        elapsed = time.perf_counter() - start
        return {"latency_seconds": elapsed, "eval_count": getattr(response, "eval_count", None), "load_duration_ns": getattr(response, "load_duration", None)}

    results = {}
    for label, model in (("vlm", settings.VLM_MODEL), ("llm", settings.LLM_MODEL)):
        messages = [{"role": "user", "content": "Reply with the single word: ready."}]
        try:
            client.chat(model=model, messages=messages, keep_alive=0, options={"num_predict": 5})
        except Exception:
            pass
        residency_after_unload = [m.model for m in client.ps().models]

        cold = _measure(model, messages, keep_alive="5m")
        residency_after_cold_call = [m.model for m in client.ps().models]
        warm = _measure(model, messages, keep_alive="5m")

        results[label] = {
            "model": model,
            "residency_after_forced_unload": residency_after_unload,
            "cold_call": cold,
            "residency_after_cold_call": residency_after_cold_call,
            "warm_call": warm,
        }

    result = {"scenario": "OLLAMA_WARM_COLD", "environment": collect_machine_metadata(), "results": results}
    _write_result(output_dir, "warm_cold", result)
    return result


def run_loop_a_stage_diagnostic(video_path: Path, output_dir: Path) -> dict:
    """Phase C — Loop-A Tail-Latency Investigation. Real Loop A + real VLM
    contention (semantic_mode="vlm" — the SAME configuration that
    originally produced this project's own measured 61.28s/38.98s
    frame-latency spikes, see SPRINT0_CPU_LOAD_TEST_REPORT.md), now
    instrumented at PER-STAGE granularity to determine WHERE inside Loop A
    a catastrophic stall actually occurs (detection / tracking / optical
    flow / crowd metrics / acute-hazard signal / risk state / trigger
    evaluation / semantic-admission submission), rather than only the
    previously-available whole-frame total. Benchmark-only instrumentation
    (see _run_benchmark_driver's own docstring) — never added to
    production code."""
    stage_timings: dict[str, list[float]] = {}
    sampler = ResourceSampler()
    sampler.start()
    driver_result = _run_benchmark_driver(video_path, semantic_mode="vlm", stage_timings=stage_timings)
    resources = sampler.stop()

    stage_summary = {stage: latency_summary(values) for stage, values in stage_timings.items()}
    stage_sum_of_means = sum(
        (s["mean"] or 0.0) for s in stage_summary.values()
    )
    whole_frame_mean = (driver_result.get("frame_latency") or {}).get("mean")

    result = {
        "scenario": "LOOP_A_STAGE_DIAGNOSTIC", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "loop_a": driver_result, "resources": resources,
        "stage_latency": stage_summary,
        "diagnostic_note": (
            "stage_latency's own 'detection'/'tracking' keys are NOT part of "
            "loop_a.frame_latency (which starts timing AFTER detect+track, "
            "see module docstring) — compare against the SUM of optical_flow"
            "+crowd_metrics+acute_hazard+risk_state+trigger_evaluation instead "
            "for a like-for-like check against frame_latency's own mean/max."
        ),
        "post_detect_stage_sum_of_means": sum(
            (stage_summary.get(k) or {}).get("mean") or 0.0
            for k in ("optical_flow", "crowd_metrics", "acute_hazard", "risk_state", "trigger_evaluation")
        ),
        "whole_frame_latency_mean_for_comparison": whole_frame_mean,
    }
    _write_result(output_dir, "loop_a_stage_diagnostic", result)
    return result


def run_admission_queue_comparison(video_path: Path, output_dir: Path) -> dict:
    """Phase D — OLD vs NEW semantic-admission benchmark regression.

    OLD: the ALREADY-RECORDED prior-phase trigger_frequency_sweep.json
    (drop-on-cap via a bare threading.Semaphore, LOW/MODERATE/HIGH real
    drop rates 20%/60%/70%) — read from disk, never re-simulated, so this
    comparison is against genuinely real prior measurements, not a
    reconstruction.

    NEW: the SAME LOW/MODERATE/HIGH operator-trigger cadence, same real
    video, but routed through the REAL, UNMODIFIED AnalysisOrchestrator
    (which now uses SemanticAdmissionQueue — see analysis_orchestrator.py)
    instead of the benchmark's own standalone driver, so this genuinely
    exercises the NEW bounded/freshness-aware admission queue, not a
    reimplementation of it."""
    old_sweep_path = output_dir / "trigger_frequency_sweep.json"
    old_sweep = None
    if old_sweep_path.exists():
        old_sweep = json.loads(old_sweep_path.read_text(encoding="utf-8"))

    levels = {"LOW": 15.0, "MODERATE": 7.0, "HIGH": 3.0}
    new_results = {}
    for level, interval in levels.items():
        db = SessionLocal()
        try:
            user = db.query(User).first()
            video = db.get(VideoAsset, _PEOPLE_CLIP_VIDEO_ID)
            session = session_service.create_session(db, video.id, user.id)
            session = session_service.start_session(db, session)
            session_id = session.id
        finally:
            db.close()

        # Reuses the same real, unmodified OPERATOR-trigger technique as
        # run_trigger_frequency_sweep — but injected via TriggerEngine.evaluate
        # itself is NOT available for the real orchestrator (it decides
        # operator_requested internally from AnalysisSession state, which has
        # no real UI path yet — Decision #8). Instead this monkeypatches
        # TriggerEngine.evaluate to inject operator_requested=True at the same
        # controlled cadence, restored immediately after — the SAME class of
        # temporary, restored monkeypatch already used by
        # _instrument_trigger_engine_for_frame_latency, not a production change.
        real_evaluate = TriggerEngine.evaluate
        state = {"last_ts": None}

        def _forced_evaluate(self, crowd_metrics, risk_state, operator_requested=False, acute_hazard_signal=None, _interval=interval):
            ts = risk_state.timestamp_seconds
            forced = state["last_ts"] is None or (ts - state["last_ts"]) >= _interval
            if forced:
                state["last_ts"] = ts
            return real_evaluate(self, crowd_metrics, risk_state, operator_requested=forced, acute_hazard_signal=acute_hazard_signal)

        frame_latencies: list[float] = []
        sampler = ResourceSampler()
        sampler.start()
        wall_start = time.perf_counter()
        TriggerEngine.evaluate = _forced_evaluate
        try:
            with _instrument_trigger_engine_for_frame_latency(frame_latencies):
                orchestrator = AnalysisOrchestrator(session_id)
                orchestrator.run()
        finally:
            TriggerEngine.evaluate = real_evaluate
        total_wall_seconds = time.perf_counter() - wall_start
        resources = sampler.stop()
        queue_metrics = orchestrator._semantic_queue.metrics_snapshot()

        db = SessionLocal()
        try:
            from app.models.decision import DecisionResultRow
            from app.models.evidence import EvidencePackage

            packages = db.query(EvidencePackage).filter(EvidencePackage.session_id == session_id).all()
            decisions = db.query(DecisionResultRow).join(EvidencePackage).filter(EvidencePackage.session_id == session_id).all()
        finally:
            db.close()

        new_results[level] = {
            "trigger_interval_seconds": interval,
            "total_wall_seconds": total_wall_seconds,
            "loop_a_effective_fps": None,
            "frame_latency": latency_summary(frame_latencies),
            "resources": resources,
            "semantic_queue_metrics": queue_metrics,
            "evidence_package_count": len(packages),
            "decision_count": len(decisions),
        }

    result = {
        "scenario": "ADMISSION_QUEUE_OLD_VS_NEW", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(),
        "old_drop_on_cap": old_sweep["levels"] if old_sweep else None,
        "new_bounded_admission_queue": new_results,
    }
    _write_result(output_dir, "admission_queue_old_vs_new", result)
    return result


def _run_stage_diagnostic_config(video_path: Path, label: str) -> dict:
    """Shared helper (Final CPU Stabilization phase, Step 3/5/6): runs the
    SAME real Loop-A + real VLM-contention scenario that originally
    reproduced the catastrophic tail spike, with per-stage timing, under
    whatever settings.OLLAMA_NUM_THREAD / settings.DIS_PRESET the CALLER has
    already set (via monkeypatch) before invoking this — this function
    itself does not change any setting."""
    stage_timings: dict[str, list[float]] = {}
    sampler = ResourceSampler()
    sampler.start()
    driver_result = _run_benchmark_driver(video_path, semantic_mode="vlm", stage_timings=stage_timings)
    resources = sampler.stop()
    stage_summary = {stage: latency_summary(values) for stage, values in stage_timings.items()}
    return {
        "label": label,
        "ollama_num_thread": settings.OLLAMA_NUM_THREAD,
        "dis_preset": settings.DIS_PRESET,
        "loop_a": driver_result,
        "resources": resources,
        "stage_latency": stage_summary,
    }


def run_thread_cap_sweep(video_path: Path, output_dir: Path, thread_caps: list[Optional[int]]) -> dict:
    """Step 3 — small, controlled sweep of settings.OLLAMA_NUM_THREAD (a
    VERIFIED real field on the installed ollama client's Options model —
    see config.py's own docstring), each run using the SAME real Loop-A +
    real VLM-contention scenario Phase C already used to reproduce the
    catastrophic tail spike. settings.OLLAMA_NUM_THREAD is monkeypatched
    for the duration of each run and restored immediately after —
    config.py's own default is only changed by a human editing the file,
    never by this benchmark."""
    original = settings.OLLAMA_NUM_THREAD
    results = {}
    try:
        for cap in thread_caps:
            settings.OLLAMA_NUM_THREAD = cap
            label = "unrestricted" if cap is None else f"num_thread={cap}"
            print(f"--- thread-cap-sweep: {label} ---", file=sys.stderr)
            results[label] = _run_stage_diagnostic_config(video_path, label)
    finally:
        settings.OLLAMA_NUM_THREAD = original

    result = {
        "scenario": "OLLAMA_THREAD_CAP_SWEEP", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "configs": results,
    }
    _write_result(output_dir, "thread_cap_sweep", result)
    return result


def run_dis_preset_sweep(video_path: Path, output_dir: Path, presets: list[str]) -> dict:
    """Step 5 — small DIS preset comparison (current "fast" vs a cheaper
    preset), under the SAME real VLM-contention condition that surfaces the
    pathology. settings.DIS_PRESET is monkeypatched per run, restored
    after."""
    original = settings.DIS_PRESET
    results = {}
    try:
        for preset in presets:
            settings.DIS_PRESET = preset
            label = f"dis_preset={preset}"
            print(f"--- dis-preset-sweep: {label} ---", file=sys.stderr)
            results[label] = _run_stage_diagnostic_config(video_path, label)
    finally:
        settings.DIS_PRESET = original

    result = {
        "scenario": "DIS_PRESET_SWEEP", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(), "configs": results,
    }
    _write_result(output_dir, "dis_preset_sweep", result)
    return result


def _run_real_orchestrator_at_interval(interval: float) -> dict:
    """Shared helper, factored out of run_admission_queue_comparison's own
    per-level body (Final CPU Stabilization phase, Step 6/9/13) — runs ONE
    real AnalysisOrchestrator session with a forced OPERATOR trigger at the
    given real-second interval, using whatever settings.OLLAMA_NUM_THREAD/
    SEMANTIC_QUEUE_MAX_DEPTH/SEMANTIC_QUEUE_STALENESS_SECONDS/DIS_PRESET are
    ACTUALLY configured at call time (no monkeypatching here) — the direct
    real validation of the FINAL chosen configuration, not a simulation."""
    db = SessionLocal()
    try:
        user = db.query(User).first()
        video = db.get(VideoAsset, _PEOPLE_CLIP_VIDEO_ID)
        session = session_service.create_session(db, video.id, user.id)
        session = session_service.start_session(db, session)
        session_id = session.id
    finally:
        db.close()

    real_evaluate = TriggerEngine.evaluate
    state = {"last_ts": None}

    def _forced_evaluate(self, crowd_metrics, risk_state, operator_requested=False, acute_hazard_signal=None, _interval=interval):
        ts = risk_state.timestamp_seconds
        forced = state["last_ts"] is None or (ts - state["last_ts"]) >= _interval
        if forced:
            state["last_ts"] = ts
        return real_evaluate(self, crowd_metrics, risk_state, operator_requested=forced, acute_hazard_signal=acute_hazard_signal)

    frame_latencies: list[float] = []
    sampler = ResourceSampler()
    sampler.start()
    wall_start = time.perf_counter()
    TriggerEngine.evaluate = _forced_evaluate
    try:
        with _instrument_trigger_engine_for_frame_latency(frame_latencies):
            orchestrator = AnalysisOrchestrator(session_id)
            orchestrator.run()
    finally:
        TriggerEngine.evaluate = real_evaluate
    total_wall_seconds = time.perf_counter() - wall_start
    resources = sampler.stop()
    queue_metrics = orchestrator._semantic_queue.metrics_snapshot()

    db = SessionLocal()
    try:
        from app.models.decision import DecisionResultRow
        from app.models.evidence import EvidencePackage

        packages = db.query(EvidencePackage).filter(EvidencePackage.session_id == session_id).all()
        decisions = db.query(DecisionResultRow).join(EvidencePackage).filter(EvidencePackage.session_id == session_id).all()
    finally:
        db.close()

    return {
        "trigger_interval_seconds": interval,
        "total_wall_seconds": total_wall_seconds,
        "frame_latency": latency_summary(frame_latencies),
        "resources": resources,
        "semantic_queue_metrics": queue_metrics,
        "evidence_package_count": len(packages),
        "decision_count": len(decisions),
    }


def run_final_high_frequency_validation(video_path: Path, output_dir: Path) -> dict:
    """Step 6/9/13 — the DECISIVE real validation test: re-runs ONLY the
    HIGH-frequency (3.0s interval) real-orchestrator scenario that
    previously produced the confirmed 888.87s catastrophic Loop-A stall
    (see admission_queue_old_vs_new.json), this time against whatever
    FINAL configuration is set in config.py (OLLAMA_NUM_THREAD,
    SEMANTIC_QUEUE_MAX_DEPTH, SEMANTIC_QUEUE_STALENESS_SECONDS, DIS_PRESET)
    at the time this is run — a direct, honest before/after comparison
    against the ONE real condition already confirmed to reliably reproduce
    catastrophic damage, not a fresh scenario that might not reproduce it
    at all (see the thread-cap-sweep's own honest non-reproducibility
    finding)."""
    new_result = _run_real_orchestrator_at_interval(3.0)
    result = {
        "scenario": "FINAL_HIGH_FREQUENCY_VALIDATION", "video": video_metadata(video_path),
        "environment": collect_machine_metadata(),
        "prior_high_frequency_result_for_comparison": {
            "note": "from admission_queue_old_vs_new.json's own NEW/HIGH entry — depth=2, OLLAMA_NUM_THREAD=None",
            "frame_latency_max_seconds": 888.8748257999996,
            "total_wall_seconds": 1567.2040068999995,
            "queue_wait_seconds_max": 945.3590000000004,
        },
        "final_configuration_result": new_result,
    }
    _write_result(output_dir, "final_high_frequency_validation", result)
    return result


def run_final_config_validation(video_path: Path, output_dir: Path) -> dict:
    """Step 6/13 — validates the FINAL selected configuration (whatever
    settings.OLLAMA_NUM_THREAD/settings.DIS_PRESET/SEMANTIC_QUEUE_* are set
    to in config.py at the time this is run — no monkeypatching here, this
    exercises the actual configured defaults) against the same real
    VLM-contention scenario, for a direct before/after comparison against
    the ORIGINAL loop_a_stage_diagnostic.json (config A: unrestricted
    Ollama + DIS "fast" + queue depth=2)."""
    result = _run_stage_diagnostic_config(video_path, "final_configuration")
    result["scenario"] = "FINAL_CONFIG_VALIDATION"
    result["video"] = video_metadata(video_path)
    result["environment"] = collect_machine_metadata()
    _write_result(output_dir, "final_config_validation", result)
    return result


# ============================================================
# CLI
# ============================================================

def main() -> None:
    parser = argparse.ArgumentParser(description="Sprint-0 CPU/Concurrency/Ollama load test")
    parser.add_argument("scenario", choices=["baseline", "vlm", "reasoner", "full-chain", "sweep", "multi-session", "warm-cold", "stage-diagnostic", "admission-compare", "thread-cap-sweep", "dis-preset-sweep", "final-config-validate", "final-high-freq-validate"])
    parser.add_argument("--output-dir", type=str, default=str(REPO_ROOT / "benchmark_results"))
    parser.add_argument("--session-count", type=int, default=2, help="multi-session only")
    args = parser.parse_args()

    output_dir = Path(args.output_dir)
    video_path = _resolve_benchmark_video()

    if args.scenario == "baseline":
        result = run_loop_a_baseline(video_path, output_dir)
    elif args.scenario == "vlm":
        result = run_loop_a_plus_vlm(video_path, output_dir)
    elif args.scenario == "reasoner":
        result = run_loop_a_plus_vlm_plus_reasoner(video_path, output_dir)
    elif args.scenario == "full-chain":
        result = run_full_chain_via_orchestrator(video_path, output_dir)
    elif args.scenario == "sweep":
        result = run_trigger_frequency_sweep(video_path, output_dir)
    elif args.scenario == "multi-session":
        result = run_multi_session(video_path, output_dir, args.session_count)
    elif args.scenario == "warm-cold":
        result = run_warm_cold_investigation(output_dir)
    elif args.scenario == "stage-diagnostic":
        result = run_loop_a_stage_diagnostic(video_path, output_dir)
    elif args.scenario == "admission-compare":
        result = run_admission_queue_comparison(video_path, output_dir)
    elif args.scenario == "thread-cap-sweep":
        result = run_thread_cap_sweep(video_path, output_dir, thread_caps=[None, 4, 8, 12])
    elif args.scenario == "dis-preset-sweep":
        result = run_dis_preset_sweep(video_path, output_dir, presets=["fast", "ultrafast"])
    elif args.scenario == "final-config-validate":
        result = run_final_config_validation(video_path, output_dir)
    elif args.scenario == "final-high-freq-validate":
        result = run_final_high_frequency_validation(video_path, output_dir)
    else:
        raise ValueError(args.scenario)

    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()
