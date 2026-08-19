"""Diagnostic (Reasoner Stability phase): direct real Ollama qwen3:8b
chat() calls at graduated schema/payload sizes, to empirically determine
WHY Reasoner latency increased after the structured-report schema
expansion — "measure, don't guess" precedent already established by
scripts/preview_verifier.py and scripts/preview_acute_hazard_signals.py.

Bypasses Reasoner.reason()'s retry loop entirely: each call here is ONE
direct ollama.Client.chat() invocation, so a slow call is captured as one
clean data point, never folded into a multi-attempt cumulative time.

Cases:
  A. OLD schema (pre-structured-report shape, reconstructed here ONLY for
     comparison — not the live schema) + compact NO_INCIDENT-shaped
     payload. This is what the Reasoner actually sent before this phase.
  B. NEW schema (current live _LLMDecisionDraft, includes
     event_classification/structured_report fields) + the SAME compact
     NO_INCIDENT-shaped payload as A. Isolates schema-compile/grammar
     overhead alone (generation length should be near-identical to A).
  C. NEW schema + WATCH-shaped payload (event_classification required,
     structured_report still null/not required). Middle case.
  D. NEW schema + INCIDENT/crisis-shaped payload (event_classification AND
     the full 6-field structured_report required) — the heaviest real
     case, and the one the two currently-failing
     test_reasoner.py::test_real_inference_* tests exercise.

Prints, per call: total_duration/load_duration/prompt_eval_count/
prompt_eval_duration/eval_count/eval_duration (all real Ollama response
metadata, never estimated), this script's own wall-clock measurement,
schema-validation outcome, and (case D) whether event_classification was
actually populated by the model on that attempt.

Usage: python scripts/measure_reasoner_latency.py [n_per_case]
"""

import json
import statistics
import sys
import time
import uuid
from pathlib import Path
from typing import Literal, Optional

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))

import ollama  # noqa: E402
from pydantic import BaseModel, Field, ValidationError  # noqa: E402

from app.core.config import settings  # noqa: E402
from app.pipeline.decision_result import (  # noqa: E402
    DecisionOutcome,
    RecommendationType,
    _LLMDecisionDraft,
)
from app.pipeline.evidence_package import (  # noqa: E402
    EvidencePackageResult,
    RiskStateSnapshot,
)
from app.pipeline.reasoner import SYSTEM_PROMPT, _serialize_evidence_context  # noqa: E402
from app.pipeline.risk_state import RiskState  # noqa: E402
from app.pipeline.trigger_engine import TriggerType  # noqa: E402
from app.pipeline.vision_observation import (  # noqa: E402
    CompactCrowdMetricsSummary,
    EvidenceType,
    NormalizedBoundingBox,
    ObservationCategory,
    VisionObservation,
)

_REASONING_SUMMARY_MAX_LENGTH = 1000
_RECOMMENDATION_RATIONALE_MAX_LENGTH = 500
_PROJECTION_NARRATIVE_MAX_LENGTH = 500


class _OldLLMDecisionDraft(BaseModel):
    """Reconstruction of _LLMDecisionDraft AS IT EXISTED before this phase
    (no event_classification, no structured_report) — for comparison ONLY.
    Not imported from decision_result.py because that shape no longer
    exists there; hand-copied here deliberately so case A is a faithful
    "what Ollama used to be asked for" baseline."""

    evidence_cited: list[str] = Field(min_length=1)
    outcome: Literal[DecisionOutcome.INCIDENT, DecisionOutcome.WATCH, DecisionOutcome.NO_INCIDENT]
    reasoning_summary: str = Field(max_length=_REASONING_SUMMARY_MAX_LENGTH)
    recommendation: Optional[RecommendationType] = None
    recommendation_rationale: Optional[str] = Field(default=None, max_length=_RECOMMENDATION_RATIONALE_MAX_LENGTH)
    projection_narrative: Optional[str] = Field(default=None, max_length=_PROJECTION_NARRATIVE_MAX_LENGTH)


def _package(risk_score, risk_state, vision_observations, trigger_reason="test trigger") -> EvidencePackageResult:
    return EvidencePackageResult(
        package_id=uuid.uuid4(),
        schema_version="1.2",
        session_id=uuid.uuid4(),
        frame_number=0,
        timestamp_seconds=0.0,
        trigger_type=TriggerType.RISK,
        trigger_reason=trigger_reason,
        model_config_id=uuid.uuid4(),
        crowd_metrics_summary=CompactCrowdMetricsSummary(
            risk_score=risk_score, risk_state=risk_state, max_density=0.1, max_pressure=10.0,
            pressure_units_disclaimer="PIXEL-SPACE UNITS - NOT CALIBRATED TO METERS",
            congested_cell_fraction=0.1, reverse_flow_cell_fraction=0.0,
            bottleneck_signal_present=True, density_confidence=0.9,
        ),
        risk_state_snapshot=RiskStateSnapshot(
            frame_number=0, timestamp_seconds=0.0, state=risk_state.value, risk_score=risk_score,
        ),
        vision_observations=vision_observations,
        vlm_call_succeeded=True,
        confidence=0.85,
        binding_constraint="density_estimation_confidence",
        complete=True,
        missing=[],
        contradictions=[],
        predictive_projection_snapshot=None,
    )


def _hazard_observation() -> VisionObservation:
    return VisionObservation(
        observation_id=uuid.uuid4(),
        category=ObservationCategory.VISIBLE_COMPRESSION,
        description=(
            "Dense crowd tightly compressed against a barrier with people "
            "visibly unable to move, several appear distressed."
        ),
        region=NormalizedBoundingBox(x_min=0.2, y_min=0.2, x_max=0.7, y_max=0.7),
        confidence=0.9,
        evidence_type=EvidenceType.DIRECT,
    )


_COMPACT_PACKAGE = _package(risk_score=25.0, risk_state=RiskState.NORMAL, vision_observations=[])
_WATCH_PACKAGE = _package(
    risk_score=45.0, risk_state=RiskState.ELEVATED, vision_observations=[],
    trigger_reason="risk state escalated NORMAL->ELEVATED",
)
_CRISIS_PACKAGE = _package(
    risk_score=95.0, risk_state=RiskState.CRITICAL, vision_observations=[_hazard_observation()],
    trigger_reason="risk state escalated ELEVATED->CRITICAL",
)

CASES = {
    "A_old_schema_compact": (_OldLLMDecisionDraft, _COMPACT_PACKAGE),
    "B_new_schema_compact": (_LLMDecisionDraft, _COMPACT_PACKAGE),
    "C_new_schema_watch": (_LLMDecisionDraft, _WATCH_PACKAGE),
    "D_new_schema_incident_crisis": (_LLMDecisionDraft, _CRISIS_PACKAGE),
}


def _run_one_call(client: ollama.Client, draft_cls, package: EvidencePackageResult) -> dict:
    schema = draft_cls.model_json_schema()
    schema_char_count = len(json.dumps(schema))
    evidence_context = _serialize_evidence_context(package)
    user_content = (
        "Evidence Package (already-computed by other system components — "
        "informational context only; do not recompute, contradict, or "
        f"restate these as your own measurement):\n{evidence_context}"
    )

    wall_start = time.perf_counter()
    response = client.chat(
        model=settings.LLM_MODEL,
        messages=[
            {"role": "system", "content": SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
        ],
        format=schema,
        options={"temperature": settings.LLM_TEMPERATURE},
        think=False,
    )
    wall_seconds = time.perf_counter() - wall_start

    content = response.message.content or ""
    validation_ok = True
    validation_error = None
    event_classification_present = None
    try:
        draft = draft_cls.model_validate_json(content)
        if hasattr(draft, "event_classification"):
            event_classification_present = draft.event_classification is not None
    except ValidationError as exc:
        validation_ok = False
        validation_error = str(exc)[:300]

    def _ns_to_s(v):
        return (v / 1e9) if v is not None else None

    return {
        "wall_seconds": wall_seconds,
        "total_duration_s": _ns_to_s(response.total_duration),
        "load_duration_s": _ns_to_s(response.load_duration),
        "prompt_eval_count": response.prompt_eval_count,
        "prompt_eval_duration_s": _ns_to_s(response.prompt_eval_duration),
        "eval_count": response.eval_count,
        "eval_duration_s": _ns_to_s(response.eval_duration),
        "schema_char_count": schema_char_count,
        "response_char_count": len(content),
        "validation_ok": validation_ok,
        "validation_error": validation_error,
        "event_classification_present": event_classification_present,
    }


def main() -> None:
    n_per_case = int(sys.argv[1]) if len(sys.argv) > 1 else 3
    timeout_override = float(sys.argv[2]) if len(sys.argv) > 2 else settings.LLM_REQUEST_TIMEOUT_SECONDS
    case_filter = sys.argv[3] if len(sys.argv) > 3 else None
    print(f"LLM_MODEL={settings.LLM_MODEL!r} OLLAMA_BASE_URL={settings.OLLAMA_BASE_URL!r}")
    print(f"LLM_REQUEST_TIMEOUT_SECONDS (configured)={settings.LLM_REQUEST_TIMEOUT_SECONDS} "
          f"(diagnostic client timeout used this run={timeout_override}) LLM_TEMPERATURE={settings.LLM_TEMPERATURE}")
    print(f"n_per_case={n_per_case} case_filter={case_filter!r}")
    print()

    client = ollama.Client(host=settings.OLLAMA_BASE_URL, timeout=timeout_override)

    cases = {k: v for k, v in CASES.items() if case_filter is None or k == case_filter}
    all_results: dict[str, list[dict]] = {}
    for case_name, (draft_cls, package) in cases.items():
        print(f"=== {case_name} ===")
        results = []
        for i in range(n_per_case):
            try:
                r = _run_one_call(client, draft_cls, package)
            except Exception as exc:  # noqa: BLE001 - diagnostic script, want to see and continue
                print(f"  call {i + 1}/{n_per_case}: EXCEPTION {type(exc).__name__}: {exc}")
                continue
            results.append(r)
            print(
                f"  call {i + 1}/{n_per_case}: wall={r['wall_seconds']:.2f}s "
                f"total_duration={r['total_duration_s']:.2f}s load={r['load_duration_s']:.2f}s "
                f"prompt_eval_count={r['prompt_eval_count']} prompt_eval_duration={r['prompt_eval_duration_s']:.2f}s "
                f"eval_count={r['eval_count']} eval_duration={r['eval_duration_s']:.2f}s "
                f"schema_chars={r['schema_char_count']} response_chars={r['response_char_count']} "
                f"validation_ok={r['validation_ok']} event_classification_present={r['event_classification_present']}"
            )
            if not r["validation_ok"]:
                print(f"    validation_error={r['validation_error']}")
        all_results[case_name] = results
        print()

    print("=== Summary (steady-state, all calls treated equally — no warm/cold split; see notes below) ===")
    for case_name, results in all_results.items():
        if not results:
            print(f"  {case_name}: NO SUCCESSFUL CALLS")
            continue
        walls = [r["wall_seconds"] for r in results]
        evals = [r["eval_count"] for r in results if r["eval_count"] is not None]
        print(
            f"  {case_name}: n={len(results)} wall min={min(walls):.2f}s max={max(walls):.2f}s "
            f"mean={statistics.mean(walls):.2f}s"
            + (f" | eval_count min={min(evals)} max={max(evals)} mean={statistics.mean(evals):.1f}" if evals else "")
        )

    print()
    print("First call per case includes Ollama model-load time if the model was evicted between scripts;")
    print("compare total_duration vs load_duration per-call above to distinguish cold-load from real generation cost.")


if __name__ == "__main__":
    main()
