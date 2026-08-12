"""Heatmap generation + persistence (Phase 12) — the I/O layer around
heatmap_rendering.py's pure functions. Encodes each rendered image as JPEG,
writes it to HEATMAP_STORAGE_PATH, and creates the corresponding
HeatmapSnapshot DB row with the REAL file size read back from disk (never
estimated) — per master spec §13/§21/§30, Postgres stores only references/
metadata, never bulk pixel data.

Generation is an ALL-5-OR-4-OF-5 event (decision #7): Predictive is the
ONLY type that can be skipped (when `CrowdMetrics.predictive_projection` is
None — PressureProjector's window hasn't accumulated enough history yet,
per Phase 11's own "never fabricate" rule). Density/Pressure/Flow-
Congestion/Risk are always generated — Risk's own Bottleneck-unavailable
case is handled INSIDE render_risk_heatmap via weight redistribution
(Resolution 1), not via a skip here. `HeatmapGenerationResult` reports
which types were generated (with their new DB rows) and which were skipped
and why — never silently generating fewer than expected without saying so.
"""

import uuid
from dataclasses import dataclass, field
from pathlib import Path

import cv2
from sqlalchemy.orm import Session

from app.core.config import REPO_ROOT, settings
from app.models.heatmap import HeatmapSnapshot, HeatmapType
from app.pipeline.crowd_metrics import CrowdMetrics
from app.pipeline.heatmap_rendering import (
    render_density_heatmap,
    render_flow_congestion_heatmap,
    render_predictive_heatmap,
    render_pressure_heatmap,
    render_risk_heatmap,
)

# Reasonable quality/size tradeoff for a debug/analytical artifact (not a
# user-facing photo) — 90 keeps visible colormap banding minimal while
# still compressing meaningfully smaller than quality=100. Not a new env
# var; same "fixed cosmetic/format detail" category as heatmap_rendering.py's
# INTER_LINEAR/TURBO choices.
JPEG_QUALITY = 90


def get_storage_dir() -> Path:
    # Same cwd-relative-path resolution pattern as VIDEO_STORAGE_PATH
    # (video_storage.py's get_storage_dir, Phase 2/3's original fix).
    raw = Path(settings.HEATMAP_STORAGE_PATH)
    path = raw if raw.is_absolute() else REPO_ROOT / raw
    path.mkdir(parents=True, exist_ok=True)
    return path


@dataclass
class HeatmapGenerationResult:
    generated: dict[HeatmapType, HeatmapSnapshot] = field(default_factory=dict)
    skipped: dict[HeatmapType, str] = field(default_factory=dict)  # type -> reason


def _write_and_persist(
    db: Session,
    session_id: uuid.UUID,
    heatmap_type: HeatmapType,
    frame_number: int,
    timestamp_seconds: float,
    image,
) -> HeatmapSnapshot:
    storage_dir = get_storage_dir()
    # {session_id}/{heatmap_type}_{frame_number}.jpg — collision-free across
    # frame_numbers (a video's frame index is unique per session) and across
    # sessions (each gets its own subdirectory).
    relative_path = f"{session_id}/{heatmap_type.value}_{frame_number}.jpg"
    destination = storage_dir / relative_path
    destination.parent.mkdir(parents=True, exist_ok=True)

    success, encoded = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not success:
        raise RuntimeError(f"Failed to JPEG-encode the {heatmap_type.value} heatmap")
    destination.write_bytes(encoded.tobytes())

    # Real size read back from disk, per this module's own docstring — never
    # estimated from the in-memory encoded buffer alone (that buffer IS what
    # got written here, but reading it back from disk is the honest source
    # of truth for what a downstream consumer will actually receive).
    file_size_bytes = destination.stat().st_size

    snapshot = HeatmapSnapshot(
        session_id=session_id,
        heatmap_type=heatmap_type,
        frame_number=frame_number,
        timestamp_seconds=timestamp_seconds,
        file_path=relative_path,
        file_size_bytes=file_size_bytes,
    )
    db.add(snapshot)
    db.commit()
    db.refresh(snapshot)
    return snapshot


def generate_and_persist_heatmaps(
    db: Session,
    session_id: uuid.UUID,
    frame_number: int,
    timestamp_seconds: float,
    crowd_metrics: CrowdMetrics,
    frame_width: int,
    frame_height: int,
) -> HeatmapGenerationResult:
    result = HeatmapGenerationResult()

    density_image = render_density_heatmap(crowd_metrics.core.density, frame_width, frame_height)
    result.generated[HeatmapType.DENSITY] = _write_and_persist(
        db, session_id, HeatmapType.DENSITY, frame_number, timestamp_seconds, density_image
    )

    pressure_image = render_pressure_heatmap(crowd_metrics.core.pressure, frame_width, frame_height)
    result.generated[HeatmapType.PRESSURE] = _write_and_persist(
        db, session_id, HeatmapType.PRESSURE, frame_number, timestamp_seconds, pressure_image
    )

    flow_congestion_image = render_flow_congestion_heatmap(
        crowd_metrics.congestion, crowd_metrics.core.flow, frame_width, frame_height
    )
    result.generated[HeatmapType.FLOW_CONGESTION] = _write_and_persist(
        db, session_id, HeatmapType.FLOW_CONGESTION, frame_number, timestamp_seconds,
        flow_congestion_image,
    )

    risk_image = render_risk_heatmap(
        crowd_metrics.core.pressure,
        crowd_metrics.congestion,
        crowd_metrics.bottleneck,
        crowd_metrics.reverse_flow,
        frame_width,
        frame_height,
    )
    result.generated[HeatmapType.RISK] = _write_and_persist(
        db, session_id, HeatmapType.RISK, frame_number, timestamp_seconds, risk_image
    )

    if crowd_metrics.predictive_projection is None:
        result.skipped[HeatmapType.PREDICTIVE] = (
            "predictive_projection is None: PressureProjector's rolling "
            "window has not accumulated enough history yet (Phase 11) — "
            "never fabricated, skipped for this generation event"
        )
    else:
        predictive_image = render_predictive_heatmap(
            crowd_metrics.core.pressure,
            crowd_metrics.predictive_projection,
            frame_width,
            frame_height,
        )
        result.generated[HeatmapType.PREDICTIVE] = _write_and_persist(
            db, session_id, HeatmapType.PREDICTIVE, frame_number, timestamp_seconds,
            predictive_image,
        )

    return result
