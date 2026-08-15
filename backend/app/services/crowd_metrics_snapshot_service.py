"""Persistence + query layer for CrowdMetricsSnapshot (Phase 21, Gap 1).
Mirrors risk_state_service.py's I/O-around-pure-logic pattern: this is the
ONLY module that writes crowd_metrics_snapshots rows, and it is called by
the AnalysisOrchestrator at the SAME periodic checkpoint already used for
progress/heatmap-cadence — never per-frame.
"""

import uuid

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.crowd_metrics_snapshot import CrowdMetricsSnapshot
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.risk_state import RiskStateResult


def persist_snapshot(
    db: Session,
    session_id: uuid.UUID,
    crowd_metrics: CrowdMetrics,
    risk_result: RiskStateResult,
) -> CrowdMetricsSnapshot:
    """Reads already-computed in-memory values only — no new computation.
    Does NOT call db.commit() itself: the orchestrator's own periodic
    checkpoint already commits once after this call (same transaction as
    the progress-column update), avoiding an extra round trip."""
    snapshot = CrowdMetricsSnapshot(
        session_id=session_id,
        frame_number=risk_result.frame_number,
        timestamp_seconds=risk_result.timestamp_seconds,
        risk_score=risk_result.risk_score,
        risk_state=risk_result.state,
        max_density=float(crowd_metrics.core.density.grid.max()),
        max_pressure=crowd_metrics.core.pressure.max_pressure,
        congested_cell_fraction=crowd_metrics.congestion.congested_cell_fraction,
        reverse_flow_cell_fraction=crowd_metrics.reverse_flow.reverse_flow_cell_fraction,
        bottleneck_signal_present=crowd_metrics.bottleneck is not None,
        density_confidence=crowd_metrics.core.density.estimation_confidence,
    )
    db.add(snapshot)
    return snapshot


def get_latest_snapshot(db: Session, session_id: uuid.UUID) -> CrowdMetricsSnapshot | None:
    return (
        db.query(CrowdMetricsSnapshot)
        .filter(CrowdMetricsSnapshot.session_id == session_id)
        .order_by(CrowdMetricsSnapshot.timestamp_seconds.desc())
        .first()
    )


def get_snapshot_nearest_timestamp(
    db: Session, session_id: uuid.UUID, timestamp_seconds: float
) -> CrowdMetricsSnapshot | None:
    """Decision #3: for a COMPLETED session, "current risk" reads the
    snapshot nearest the video player's CURRENT PLAYBACK TIMESTAMP rather
    than the latest row overall. Ties (equal absolute distance) resolve to
    the earlier snapshot — an arbitrary but deterministic choice; both
    would show functionally the same risk_score/risk_state, since a
    genuine ELEVATED/CRITICAL transition needs RISK_STATE_PERSISTENCE_FRAMES
    consecutive frames (~1s) to confirm, which is bigger than a single
    checkpoint tie."""
    return (
        db.query(CrowdMetricsSnapshot)
        .filter(CrowdMetricsSnapshot.session_id == session_id)
        .order_by(
            func.abs(CrowdMetricsSnapshot.timestamp_seconds - timestamp_seconds),
            CrowdMetricsSnapshot.timestamp_seconds.asc(),
        )
        .first()
    )


# Chart-friendliness (new implementation decision, this phase's own
# judgment call, documented per the spec's request): a full session's worth
# of snapshots (~1 row/second at PROGRESS_UPDATE_INTERVAL_FRAMES=30 frames /
# typical 30fps video) is already sparse enough for a browser-rendered line
# chart even on a long video (a 30-minute session is only ~1800 rows) — no
# server-side downsampling is applied by default. DEFAULT_TIMESERIES_LIMIT
# exists as a defensive cap (not a real-world-triggered downsampling need
# yet) against a pathological/unexpectedly-long session sending an
# unbounded response; `limit` is exposed as a caller-overridable parameter
# so the route can accept a query param without duplicating this constant.
DEFAULT_TIMESERIES_LIMIT = 5000


def get_session_crowd_metrics_timeseries(
    db: Session, session_id: uuid.UUID, limit: int = DEFAULT_TIMESERIES_LIMIT
) -> list[CrowdMetricsSnapshot]:
    return (
        db.query(CrowdMetricsSnapshot)
        .filter(CrowdMetricsSnapshot.session_id == session_id)
        .order_by(CrowdMetricsSnapshot.timestamp_seconds.asc())
        .limit(limit)
        .all()
    )
