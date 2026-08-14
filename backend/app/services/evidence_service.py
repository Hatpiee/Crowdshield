"""Persistence layer for EvidencePackageResult (Phase 16, Step 4). Mirrors
heatmap_service.py / risk_state_service.py's I/O-around-pure-logic pattern:
EvidenceBuilder itself only touches the filesystem for its own image saves
(see evidence_builder.py); this module is the ONLY place an EvidencePackage/
EvidenceItem DB row gets written.

RESOLUTION 4 (immutability is an application-layer guarantee): there is
DELIBERATELY no update/modify function anywhere in this module for an
already-persisted evidence_packages or evidence_items row — "once
persisted, an Evidence Package is immutable" (§16) is enforced by this
absence, not by a database trigger/constraint. See
test_evidence_persistence.py's source-level check.
"""

import dataclasses
import uuid
from dataclasses import dataclass, field

from sqlalchemy.orm import Session

from app.models.evidence import EvidenceItem, EvidencePackage
from app.pipeline.evidence_package import EvidencePackageResult


def persist_evidence_package(db: Session, result: EvidencePackageResult) -> EvidencePackage:
    package = EvidencePackage(
        id=result.package_id,
        schema_version=result.schema_version,
        session_id=result.session_id,
        frame_number=result.frame_number,
        timestamp_seconds=result.timestamp_seconds,
        trigger_type=result.trigger_type,
        trigger_reason=result.trigger_reason,
        model_config_id=result.model_config_id,
        crowd_metrics_summary=result.crowd_metrics_summary.model_dump(mode="json"),
        risk_state_snapshot=dataclasses.asdict(result.risk_state_snapshot),
        # Named vision_observations_present in the DB column — semantically
        # IS vlm_call_succeeded (decision #2's distinct-from-empty-list
        # state): True even when the VLM call succeeded and genuinely
        # returned zero observations, False only when the call itself
        # failed.
        vision_observations_present=result.vlm_call_succeeded,
        confidence=result.confidence,
        binding_constraint=result.binding_constraint,
        complete=result.complete,
        missing_evidence=result.missing,
        contradictions=[dataclasses.asdict(c) for c in result.contradictions],
        representative_frame_path=result.representative_frame_path,
        roi_crop_path=result.roi_crop_path,
        roi_bbox=list(result.roi_bbox),
        predictive_projection_snapshot=(
            dataclasses.asdict(result.predictive_projection_snapshot)
            if result.predictive_projection_snapshot is not None
            else None
        ),
    )
    db.add(package)
    # Flush before adding dependent EvidenceItem rows: both PKs are
    # client-assigned (no autoincrement to force ordering), and no
    # relationship() is declared between the two models (deliberately kept
    # simple — see this module's docstring), so an explicit flush is what
    # guarantees the evidence_packages row exists before evidence_items rows
    # referencing it are inserted, in the same transaction.
    db.flush()

    for observation in result.vision_observations:
        db.add(
            EvidenceItem(
                evidence_package_id=package.id,
                observation_id=observation.observation_id,
                category=observation.category,
                confidence=observation.confidence,
                evidence_type=observation.evidence_type,
                description=observation.description,
                region_x_min=observation.region.x_min,
                region_y_min=observation.region.y_min,
                region_x_max=observation.region.x_max,
                region_y_max=observation.region.y_max,
            )
        )

    db.commit()
    db.refresh(package)
    return package


@dataclass
class EvidencePackageWithItems:
    package: EvidencePackage
    items: list[EvidenceItem] = field(default_factory=list)


def _load_items(db: Session, package_id: uuid.UUID) -> list[EvidenceItem]:
    return (
        db.query(EvidenceItem)
        .filter(EvidenceItem.evidence_package_id == package_id)
        .order_by(EvidenceItem.created_at.asc())
        .all()
    )


def get_session_evidence_packages(db: Session, session_id: uuid.UUID) -> list[EvidencePackageWithItems]:
    rows = (
        db.query(EvidencePackage)
        .filter(EvidencePackage.session_id == session_id)
        .order_by(EvidencePackage.created_at.desc())
        .all()
    )
    return [EvidencePackageWithItems(package=row, items=_load_items(db, row.id)) for row in rows]


def get_evidence_package(db: Session, evidence_package_id: uuid.UUID) -> EvidencePackageWithItems | None:
    row = db.get(EvidencePackage, evidence_package_id)
    if row is None:
        return None
    return EvidencePackageWithItems(package=row, items=_load_items(db, row.id))
