"""Deterministic verification pre-checks — Phase 18, Decision A.

Two of §18's six named checks are fully deterministic and computable
directly from already-known fields — no semantic judgment required, so no
LLM call is needed or appropriate (this project's "Deterministic Before
Generative" principle, same discipline as Phase 17's should_abstain()):

- confidence_consistency: `decision.confidence` must EXACTLY equal
  `evidence_package.confidence` (Frozen Decision: confidence is propagated
  by direct inheritance, never invented — see decision_result.py /
  reasoner.py). If this ever fails, it indicates a REAL BUG in Phase 17's
  propagation code, not a model behavior issue — Verifier.verify() flags
  this distinction explicitly when it happens (see verifier.py).
- evidence_grounding (EXISTENCE portion only — the semantic "is this
  citation actually RELEVANT" judgment is one of the four LLM-checked
  dimensions, unsupported_claims): every string in `decision.evidence_cited`
  must correspond to an actual `compact_metrics` field name OR an actual
  `observation_id` present in `evidence_package.vision_observations`.
  Anything else is a fabricated citation — a fact-checkable existence
  question a Python set-membership check answers exactly, requiring no
  model judgment.
"""

from app.pipeline.decision_result import DecisionResult
from app.pipeline.evidence_package import EvidencePackageResult


def check_confidence_consistency(
    decision: DecisionResult, evidence_package: EvidencePackageResult
) -> bool:
    return decision.confidence == evidence_package.confidence


def valid_citation_identifiers(evidence_package: EvidencePackageResult) -> set[str]:
    field_names = set(evidence_package.crowd_metrics_summary.model_dump(mode="json").keys())
    observation_ids = {str(obs.observation_id) for obs in evidence_package.vision_observations}
    return field_names | observation_ids


def check_evidence_grounding_existence(
    decision: DecisionResult, evidence_package: EvidencePackageResult
) -> bool:
    valid_identifiers = valid_citation_identifiers(evidence_package)
    return all(citation in valid_identifiers for citation in decision.evidence_cited)
