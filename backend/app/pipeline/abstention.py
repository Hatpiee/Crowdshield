"""Deterministic abstention check — roadmap Phase 16 Part 1 (this project's
Phase 17), §8's three named triggers.

============================================================
KEY ARCHITECTURAL DECISION — abstention is deterministic, not the LLM's call
============================================================
§8's three abstention triggers ("confidence falls below a floor, a
contradiction is unresolved, or evidence is materially incomplete") are ALL
already computable directly from Phase 16's EvidencePackage fields
(`confidence`, `contradictions` — which per Phase 16's own design always
carry `resolution_status="UNRESOLVED"` — and `complete`/`missing`).
Delegating this to the LLM's own "judgment" would mean using generative
reasoning for a question a deterministic check can already answer,
directly contradicting this project's FIRST-listed constitutional
principle, "Deterministic Before Generative." `Reasoner.reason()` calls
`should_abstain()` FIRST and short-circuits to `outcome=ABSTAIN` WITHOUT
calling the LLM at all if any condition holds — this also directly serves
CPU-feasibility (Adaptive Computation), avoiding unnecessary expensive LLM
calls on structurally unresolvable cases. Logged in DECISIONS.md as a
significant architectural decision.
"""

from typing import Optional

from app.core.config import settings
from app.pipeline.evidence_package import EvidencePackageResult


def should_abstain(evidence_package: EvidencePackageResult) -> Optional[str]:
    """Checks the three conditions in the documented order, returning the
    FIRST applicable reason (not all three combined — the first true
    condition is sufficient and simpler to reason about). Returns None when
    none apply."""
    if evidence_package.confidence <= settings.DECISION_CONFIDENCE_FLOOR:
        # INCLUSIVE comparison, deliberately (not a strict "<"): the floor
        # value itself (0.4) is set to density.py's own
        # TOO_FEW_POINTS_CONFIDENCE tier — the worst confidence this
        # pipeline ever systematically produces, essentially a guess from
        # <3 tracked points. A strict "<" would let that exact worst-known
        # tier narrowly skate through without abstaining, contradicting
        # this project's own stated philosophy that abstention exists
        # specifically to catch unreliable-input cases. See DECISIONS.md's
        # "DECISION_CONFIDENCE_FLOOR Boundary Semantics" entry.
        return (
            f"confidence={evidence_package.confidence:.3f} is at or below "
            f"DECISION_CONFIDENCE_FLOOR={settings.DECISION_CONFIDENCE_FLOOR:.3f}"
        )

    if len(evidence_package.contradictions) > 0:
        # Per Phase 16's own design, EVERY contradiction currently carries
        # resolution_status="UNRESOLVED" always (no reasoning layer existed
        # yet to resolve one) — a non-empty list unconditionally means an
        # unresolved contradiction exists.
        types = ", ".join(c.contradiction_type for c in evidence_package.contradictions)
        return f"unresolved contradiction(s) present: {types}"

    if not evidence_package.complete:
        return f"evidence is materially incomplete: missing={evidence_package.missing}"

    return None
