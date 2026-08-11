"""Crowd Pressure (master spec §12 — the most heavily validated decision in
this project): Pressure(r,t) = density(r,t) x velocity_variance(r,t),
computed POINTWISE on the shared spatial grid (decision #1), never as a
single global scalar.

============================================================
CRITICAL UNITS DISCLOSURE — READ BEFORE USING THIS MODULE'S OUTPUT
============================================================
The literature-derived Crowd Pressure thresholds (crowd turbulence
~0.02 s^-2, stampede risk ~0.04 s^-2, per §6/§12) were calibrated in
REAL-WORLD physical units: density in people/m^2, velocity in m/s. This
project has NO camera calibration or homography step anywhere in scope to
convert pixel measurements into real-world units. Every value in this
module's `grid` is therefore in PIXEL-based units (density in
"people per grid cell", velocity_variance in pixels^2/second^2, so
Pressure itself is in "people * pixels^2/second^2 per cell") — NOT the
literature's meter-based units. Applying the 0.02/0.04 threshold VALUES
literally to this pixel-space output would be meaningless without a units
conversion this project does not have. This is a genuine, structural MVP
limitation (see DECISIONS.md's "Known Structural Limitation: Pixel-Space
vs. Real-World Units" section), not solved here — this module computes
Pressure honestly in pixel-space and every CrowdPressureField carries
`units_disclaimer` alongside the data itself so this limitation cannot be
silently missed by a downstream consumer who only reads the numbers.

This module never exposes Crowd Pressure computation as something a
prompt-driven config could redirect (§12, constitutional: the LLM/VLM must
never compute or override Crowd Pressure) — it is a fixed, plain numeric
function with no configurable formula.
"""

from dataclasses import dataclass

import numpy as np

from app.pipeline.density import DensityField
from app.pipeline.flow_field import FlowGridField

UNITS_DISCLAIMER = (
    "PIXEL-SPACE UNITS, NOT METERS: this Pressure grid is density "
    "(people/cell) x velocity_variance (pixels^2/second^2). The "
    "literature's 0.02/0.04 s^-2 thresholds are calibrated in "
    "people/m^2 and m/s and are NOT directly comparable to these values "
    "without a camera calibration this project does not have. "
    "See DECISIONS.md."
)


@dataclass
class CrowdPressureField:
    frame_number: int
    timestamp_seconds: float
    grid: np.ndarray  # shape (rows, cols), Pressure(r,t) per cell, PIXEL-based units
    max_pressure: float
    mean_pressure: float
    units_disclaimer: str = UNITS_DISCLAIMER


def compute_crowd_pressure_field(
    density: DensityField, flow: FlowGridField
) -> CrowdPressureField:
    if density.grid.shape != flow.grid_velocity_variance.shape:
        raise ValueError(
            "DensityField.grid and FlowGridField.grid_velocity_variance must "
            f"have matching shapes to combine pointwise; got "
            f"{density.grid.shape} vs {flow.grid_velocity_variance.shape}"
        )

    pressure_grid = density.grid * flow.grid_velocity_variance

    return CrowdPressureField(
        frame_number=density.frame_number,
        timestamp_seconds=density.timestamp_seconds,
        grid=pressure_grid,
        max_pressure=float(pressure_grid.max()),
        mean_pressure=float(pressure_grid.mean()),
    )
