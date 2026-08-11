"""Density estimation (master spec §6/§40): Kernel Density Estimation
(primary) + Voronoi (secondary, extreme-density cross-check).

NORMALIZATION METHOD (decision #2): scipy.stats.gaussian_kde's raw output
is a continuous probability density (integrates to 1 over the whole plane,
not just the frame). To get "expected people count per cell": evaluate the
KDE at each grid cell's center, multiply by cell area to get each cell's
relative probability MASS, then RENORMALIZE so the masses sum to exactly
track_count. This differs slightly from a naive "evaluate and multiply by N"
approach: a plain Gaussian KDE has infinite support, so some of its
probability mass mathematically extends beyond the frame's edges even
though every real tracked point is, by definition, inside the frame. Since
this system's population is genuinely bounded to the frame, renormalizing
within the grid (rather than accepting a bit of "leaked" tail mass) is the
standard boundary-correction approach and gives an exact — not merely
approximate — count recovery by construction (see test_density.py).

GRACEFUL DEGRADATION (decision #3): scipy.stats.gaussian_kde requires a
non-singular covariance matrix (effectively >=3 non-coincident points in
2D); scipy.spatial.Voronoi requires >=3 non-collinear/non-coincident 2D
points. Both are wrapped explicitly — on failure this module does NOT
crash and does NOT fabricate a plausible-looking KDE/Voronoi result. It
falls back to a raw per-cell point COUNT (no smoothing), honestly labeled
via `degradation_reason`, with `estimation_confidence` lowered. Zero
tracked points is a real, valid "empty scene" measurement — full
confidence, no degradation (per §12's own failure-mode principle: partial
upstream failure widens confidence, it never silently defaults a metric to
a fabricated "normal").

INPUT FILTERING (decision #4): only tracks with is_lost=False are used.
A lost track's position is its last REAL match (Phase 7), which is
known-stale by the time it's lost — including it would inject a
potentially-outdated position into a snapshot meant to represent "where
people are right now." KNOWN LIMITATION, logged in DECISIONS.md: this also
means a just-appeared, not-yet-confirmed person (ByteTrack's own
confirmation lag, discovered in Phase 7) is briefly absent from density
too — an honest tradeoff of building on TrackingResult rather than raw
DetectionResult.
"""

from dataclasses import dataclass

import numpy as np
from scipy.spatial import QhullError, Voronoi
from scipy.stats import gaussian_kde
from shapely.geometry import Polygon, box

from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.track import TrackingResult

# Below this many points, gaussian_kde/Voronoi are not mathematically
# reliable (2D covariance needs >=3 non-degenerate points) — use the
# honest histogram fallback instead of risking a numerically unstable KDE.
MIN_POINTS_FOR_RELIABLE_ESTIMATION = 3

# Simple, documented confidence values for each degradation path (decision
# #3/#6 — "a simple, clearly-documented threshold-based rule, not an
# elaborate model").
TOO_FEW_POINTS_CONFIDENCE = 0.4
VORONOI_UNAVAILABLE_CONFIDENCE = 0.85
HIGH_DISAGREEMENT_CONFIDENCE = 0.5
HIGH_DISAGREEMENT_THRESHOLD = 0.75  # mean relative disagreement above this is "high"


@dataclass
class DensityField:
    frame_number: int
    timestamp_seconds: float
    grid: np.ndarray  # shape (rows, cols), estimated people-count per cell
    track_count: int
    estimation_confidence: float  # 0-1
    degradation_reason: str | None  # None if full confidence
    voronoi_disagreement_summary: float | None  # mean relative disagreement, or None


def _histogram_fallback_grid(
    points_xy: list[tuple[float, float]], grid: CrowdGrid
) -> np.ndarray:
    """Raw per-cell point count, no smoothing — used only when KDE cannot
    be reliably computed. Honestly a fallback, never presented as a KDE
    result."""
    density_grid = np.zeros((grid.rows, grid.cols), dtype=float)
    for x, y in points_xy:
        row, col = grid.cell_index_for_point(x, y)
        density_grid[row, col] += 1.0
    return density_grid


def _kde_grid(points_arr: np.ndarray, grid: CrowdGrid, track_count: int) -> np.ndarray:
    kde = gaussian_kde(points_arr.T)  # scipy expects shape (dims, n_samples)
    xx, yy = grid.cell_center_grid()
    positions = np.vstack([xx.ravel(), yy.ravel()])
    pdf_values = kde.evaluate(positions).reshape(grid.rows, grid.cols)

    cell_area = grid.cell_width_px * grid.cell_height_px
    mass = pdf_values * cell_area
    total_mass = mass.sum()
    if total_mass <= 0:
        return np.zeros((grid.rows, grid.cols))
    return mass / total_mass * track_count


def _voronoi_finite_polygons_2d(
    vor: Voronoi, radius: float
) -> tuple[list[list[int]], np.ndarray]:
    """Reconstructs finite region vertex-index lists for every point in a
    scipy Voronoi diagram, extending unbounded ridges out to `radius`
    (chosen far beyond the frame so the later shapely clip against the
    frame rectangle is unaffected by where exactly the ray gets cut off).

    Standard, widely-used technique for bounding an otherwise-infinite
    Voronoi diagram (the classic "voronoi_finite_polygons_2d" recipe) —
    not invented here; reimplemented directly against this project's data
    rather than taken as an external dependency.

    Returns (new_regions, new_vertices) where new_regions[i] is the list
    of vertex indices (into new_vertices) for vor.points[i]'s cell.
    """
    new_regions: list[list[int]] = []
    new_vertices = vor.vertices.tolist()

    center = vor.points.mean(axis=0)

    all_ridges: dict[int, list[tuple[int, int, int]]] = {}
    for (p1, p2), (v1, v2) in zip(vor.ridge_points, vor.ridge_vertices):
        all_ridges.setdefault(p1, []).append((p2, v1, v2))
        all_ridges.setdefault(p2, []).append((p1, v1, v2))

    for p1 in range(len(vor.points)):
        region_index = vor.point_region[p1]
        vertices = vor.regions[region_index]

        if all(v >= 0 for v in vertices):
            new_regions.append(vertices)
            continue

        ridges = all_ridges[p1]
        new_region = [v for v in vertices if v >= 0]

        for p2, v1, v2 in ridges:
            if v2 < 0:
                v1, v2 = v2, v1
            if v1 >= 0:
                continue  # finite ridge, endpoint already included

            tangent = vor.points[p2] - vor.points[p1]
            tangent = tangent / np.linalg.norm(tangent)
            normal = np.array([-tangent[1], tangent[0]])

            midpoint = vor.points[[p1, p2]].mean(axis=0)
            direction = np.sign(np.dot(midpoint - center, normal)) * normal
            far_point = vor.vertices[v2] + direction * radius

            new_region.append(len(new_vertices))
            new_vertices.append(far_point.tolist())

        vertices_arr = np.asarray([new_vertices[v] for v in new_region])
        centroid = vertices_arr.mean(axis=0)
        angles = np.arctan2(
            vertices_arr[:, 1] - centroid[1], vertices_arr[:, 0] - centroid[0]
        )
        ordered = np.array(new_region)[np.argsort(angles)]
        new_regions.append(ordered.tolist())

    return new_regions, np.asarray(new_vertices)


def _voronoi_disagreement(
    points_arr: np.ndarray, grid: CrowdGrid, kde_grid: np.ndarray
) -> float | None:
    """Per-point diagnostic (decision #6): for each tracked point, compares
    its Voronoi-cell-based local density (1 / clipped cell area) against
    the already-computed KDE grid's value at that point's cell (converted
    to the same per-unit-area units). Returns the mean relative
    disagreement across all points, or None if no point produced a usable
    comparison (e.g. every clipped cell had ~zero area).

    Raises QhullError (uncaught here, handled by the caller) if the point
    set is too degenerate for Voronoi construction at all (e.g. coincident
    or collinear points).
    """
    vor = Voronoi(points_arr)
    # Far beyond the frame in every direction, so ray truncation never
    # interferes with the frame-rectangle clip below.
    radius = max(grid.frame_width, grid.frame_height) * 10.0
    regions, vertices = _voronoi_finite_polygons_2d(vor, radius)

    frame_box = box(0, 0, grid.frame_width, grid.frame_height)
    cell_area = grid.cell_width_px * grid.cell_height_px

    relative_diffs = []
    for i, point in enumerate(points_arr):
        polygon_points = vertices[regions[i]]
        cell_polygon = Polygon(polygon_points)
        clipped_area = cell_polygon.intersection(frame_box).area
        if clipped_area <= 0:
            continue

        voronoi_density = 1.0 / clipped_area  # points per unit area

        row, col = grid.cell_index_for_point(point[0], point[1])
        kde_density_per_area = kde_grid[row, col] / cell_area

        denominator = (voronoi_density + kde_density_per_area) / 2 + 1e-12
        relative_diffs.append(abs(voronoi_density - kde_density_per_area) / denominator)

    if not relative_diffs:
        return None
    return float(np.mean(relative_diffs))


def compute_density_field(
    tracking_result: TrackingResult, grid: CrowdGrid
) -> DensityField:
    points = [(t.point.x, t.point.y) for t in tracking_result.tracks if not t.is_lost]
    track_count = len(points)

    if track_count == 0:
        return DensityField(
            frame_number=tracking_result.frame_number,
            timestamp_seconds=tracking_result.timestamp_seconds,
            grid=np.zeros((grid.rows, grid.cols), dtype=float),
            track_count=0,
            estimation_confidence=1.0,
            degradation_reason=None,
            voronoi_disagreement_summary=None,
        )

    points_arr = np.array(points, dtype=float)
    degradation_reason: str | None = None
    confidence = 1.0
    voronoi_disagreement_summary: float | None = None

    if track_count < MIN_POINTS_FOR_RELIABLE_ESTIMATION:
        density_grid = _histogram_fallback_grid(points, grid)
        degradation_reason = "too_few_points"
        confidence = TOO_FEW_POINTS_CONFIDENCE
    else:
        try:
            density_grid = _kde_grid(points_arr, grid, track_count)
        except np.linalg.LinAlgError:
            density_grid = _histogram_fallback_grid(points, grid)
            degradation_reason = "kde_singular_covariance"
            confidence = TOO_FEW_POINTS_CONFIDENCE
        else:
            try:
                voronoi_disagreement_summary = _voronoi_disagreement(
                    points_arr, grid, density_grid
                )
            except QhullError:
                degradation_reason = "voronoi_unavailable"
                confidence = min(confidence, VORONOI_UNAVAILABLE_CONFIDENCE)
            else:
                if (
                    voronoi_disagreement_summary is not None
                    and voronoi_disagreement_summary > HIGH_DISAGREEMENT_THRESHOLD
                ):
                    degradation_reason = degradation_reason or "high_voronoi_disagreement"
                    confidence = min(confidence, HIGH_DISAGREEMENT_CONFIDENCE)

    return DensityField(
        frame_number=tracking_result.frame_number,
        timestamp_seconds=tracking_result.timestamp_seconds,
        grid=density_grid,
        track_count=track_count,
        estimation_confidence=confidence,
        degradation_reason=degradation_reason,
        voronoi_disagreement_summary=voronoi_disagreement_summary,
    )
