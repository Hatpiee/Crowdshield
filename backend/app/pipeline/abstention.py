"""Deterministic abstention check — roadmap Phase 16 Part 1 (this project's
Phase 17), §8's three named triggers, extended (Acute-Hazard Precision
phase) with a FOURTH, ACUTE_HAZARD-specific condition.

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

============================================================
FOURTH CONDITION — ACUTE_HAZARD EVIDENCE-CONSISTENCY GATE (Acute-Hazard
Precision phase, real finding, see DECISIONS.md)
============================================================
A real run of the full production chain against `people_clip.mp4` found
ALL THREE real ACUTE_HAZARD triggers produced a VLM observation in the
SAME category, `VISIBLE_OBSTRUCTION` — an ordinary, routine crowd-
management category already well-handled by the pre-existing RISK trigger
path, never `VISIBLE_HAZARD` or `UNUSUAL_MOVEMENT` (the two categories in
the ALREADY-EXISTING six-category taxonomy that actually denote acute/
dynamic content). The first two happened to be caught by the two checks
above for UNRELATED, coincidental reasons (a low density-confidence
reading; the pre-existing reverse-flow contradiction); the third had
neither blocker and produced a real, unforced, false-positive `INCIDENT`
(`OBSTRUCTION_OR_BARRIER`) — see DECISIONS.md's "Acute-Hazard Precision
Phase" entry for the full real calibration/investigation. This closes
that SPECIFIC gap: an ACUTE_HAZARD-triggered package whose ONLY VLM
corroboration is a routine, non-acute category is evidence the semantic
inspection did not actually confirm an acute event — deterministically,
using ONLY already-produced EvidencePackage/VisionObservation data (never
a second LLM call, never re-analyzing images). ACUTE_HAZARD still means
"worth inspecting" (the trigger itself is untouched); this gate is what
keeps that inspection from silently becoming "therefore it is an
incident" on weak semantic grounds alone.
"""

from typing import Optional

from app.core.config import settings
from app.pipeline.evidence_package import EvidencePackageResult
from app.pipeline.trigger_engine import TriggerType
from app.pipeline.vision_observation import ObservationCategory

# The two ALREADY-EXISTING categories (out of six total — see
# vision_observation.py's ObservationCategory) that denote genuinely
# acute/dynamic content, as opposed to VISIBLE_OBSTRUCTION/BOTTLENECK/
# BARRIER/VISIBLE_COMPRESSION, which all describe routine, static-or-
# gradual crowd-management conditions the pre-existing RISK trigger path
# already handles. Not a new taxonomy — a semantic grouping of the
# existing one, informed directly by the real finding above.
_ACUTE_HAZARD_CONSISTENT_CATEGORIES = frozenset(
    {ObservationCategory.VISIBLE_HAZARD, ObservationCategory.UNUSUAL_MOVEMENT}
)


def _acute_hazard_weak_corroboration_reason(evidence_package: EvidencePackageResult) -> Optional[str]:
    if evidence_package.trigger_type != TriggerType.ACUTE_HAZARD:
        return None

    matching = [
        obs for obs in evidence_package.vision_observations
        if obs.category in _ACUTE_HAZARD_CONSISTENT_CATEGORIES
    ]
    if matching:
        return None

    observed_categories = sorted({obs.category.value for obs in evidence_package.vision_observations})
    return (
        "ACUTE_HAZARD trigger fired but VLM evidence does not corroborate "
        "with an acute-hazard-consistent observation category (found: "
        f"{observed_categories or 'no observations'}; routine crowd-"
        "management categories alone are insufficient grounds for an "
        "acute-hazard-sourced incident)"
    )


def should_abstain(evidence_package: EvidencePackageResult) -> Optional[str]:
    """Checks the four conditions in the documented order, returning the
    FIRST applicable reason (not all combined — the first true condition is
    sufficient and simpler to reason about). Returns None when none apply."""
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

    acute_hazard_reason = _acute_hazard_weak_corroboration_reason(evidence_package)
    if acute_hazard_reason is not None:
        return acute_hazard_reason

    return None
