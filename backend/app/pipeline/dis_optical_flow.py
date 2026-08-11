"""DIS (Dense Inverse Search) optical flow adapter (master spec §5/§11/§40).

VERIFIED LIBRARY API (inspected directly on the installed
`opencv-python-headless==5.0.0.93`, not assumed from memory): DIS optical
flow was promoted from opencv_contrib into OpenCV's main "video" module as
of OpenCV 4.0, so it is already available in this project's existing
opencv-python-headless install — confirmed via `dir(cv2)` that
`cv2.DISOpticalFlow_create` and the preset constants
`cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST` (0), `cv2.DISOPTICAL_FLOW_PRESET_FAST`
(1), `cv2.DISOPTICAL_FLOW_PRESET_MEDIUM` (2) all exist. No new package was
installed for this phase. `DISOpticalFlow.calc(I0, I1, flow) -> flow` was
verified end-to-end on a synthetic 5px-shifted random-noise texture pair
(measured mean dx ≈ 4.97, matching the true 5px shift).
"""

import cv2
import numpy as np

from app.core.config import settings
from app.pipeline.frame import Frame
from app.pipeline.motion import MotionResult
from app.pipeline.optical_flow import OpticalFlow

# Maps the config-level preset name to the real, verified OpenCV constant —
# never hardcode a bare int bypassing this mapping.
_PRESET_CONSTANTS = {
    "ultrafast": cv2.DISOPTICAL_FLOW_PRESET_ULTRAFAST,
    "fast": cv2.DISOPTICAL_FLOW_PRESET_FAST,
    "medium": cv2.DISOPTICAL_FLOW_PRESET_MEDIUM,
}

# Number of bins for the magnitude-weighted direction histogram used to
# compute directional_entropy — an internal implementation constant (not a
# domain calibration value like DIS_PRESET/MOTION_MAGNITUDE_NOISE_FLOOR, so
# not logged in DECISIONS.md), 16 bins is a standard, unremarkable choice
# for a 360-degree circular histogram (22.5 degrees/bin).
_DIRECTION_HISTOGRAM_BINS = 16


class UnsupportedDISPresetError(Exception):
    pass


class DISOpticalFlowAdapter(OpticalFlow):
    """See module docstring for verified library API details.

    The underlying cv2.DISOpticalFlow object is constructed ONCE, in
    __init__ (same "load once" principle as Phase 6's YOLO model loading).
    Unlike Phase 7's Tracker, compute() has no cross-call state — this
    instance may safely be reused across any frame pairs, including
    unrelated videos (see OpticalFlow's docstring).
    """

    def __init__(self) -> None:
        preset_name = settings.DIS_PRESET
        if preset_name not in _PRESET_CONSTANTS:
            raise UnsupportedDISPresetError(
                f"DIS_PRESET={preset_name!r} is not supported. Accepted "
                f"values: {sorted(_PRESET_CONSTANTS)}"
            )

        self._preset_name = preset_name
        self._dis = cv2.DISOpticalFlow_create(_PRESET_CONSTANTS[preset_name])
        self._noise_floor = settings.MOTION_MAGNITUDE_NOISE_FLOOR

    def compute(self, prev_frame: Frame, curr_frame: Frame) -> MotionResult:
        self._validate_matching_dimensions(prev_frame, curr_frame)

        # DIS operates on single-channel (grayscale) images; Frame.image is
        # BGR per Phase 5's convention — explicit conversion required here
        # (the inverse of the usual BGR/RGB footgun this project has been
        # careful about: this time it's BGR -> grayscale, not BGR -> RGB).
        prev_gray = cv2.cvtColor(prev_frame.image, cv2.COLOR_BGR2GRAY)
        curr_gray = cv2.cvtColor(curr_frame.image, cv2.COLOR_BGR2GRAY)

        flow_field = self._dis.calc(prev_gray, curr_gray, None)

        magnitude, angle_degrees = cv2.cartToPolar(
            flow_field[..., 0], flow_field[..., 1], angleInDegrees=True
        )

        mask = magnitude >= self._noise_floor
        if np.any(mask):
            filtered_magnitude = magnitude[mask]
            filtered_angle = angle_degrees[mask]

            mean_velocity = float(filtered_magnitude.mean())
            velocity_variance = float(filtered_magnitude.var())

            dominant_direction_degrees = _weighted_circular_mean_degrees(
                filtered_angle, filtered_magnitude
            )
            directional_entropy = _weighted_direction_entropy(
                filtered_angle, filtered_magnitude
            )
        else:
            # A genuinely static scene (or everything below the noise
            # floor) — zero motion is a real, valid measurement, but a
            # "dominant direction" of nothing is not: never fabricated.
            mean_velocity = 0.0
            velocity_variance = 0.0
            dominant_direction_degrees = None
            directional_entropy = None

        return MotionResult(
            frame_number=curr_frame.frame_number,
            prev_frame_number=prev_frame.frame_number,
            timestamp_seconds=curr_frame.timestamp_seconds,
            flow_field=flow_field,
            mean_velocity=mean_velocity,
            velocity_variance=velocity_variance,
            dominant_direction_degrees=dominant_direction_degrees,
            directional_entropy=directional_entropy,
            preset_used=self._preset_name,
            noise_floor_used=self._noise_floor,
        )


def _weighted_circular_mean_degrees(
    angle_degrees: np.ndarray, weights: np.ndarray
) -> float:
    """Magnitude-weighted circular mean direction, in degrees [0, 360)."""
    angle_radians = np.deg2rad(angle_degrees)
    sin_sum = float(np.sum(weights * np.sin(angle_radians)))
    cos_sum = float(np.sum(weights * np.cos(angle_radians)))
    mean_radians = np.arctan2(sin_sum, cos_sum)
    return float(np.rad2deg(mean_radians)) % 360.0


def _weighted_direction_entropy(angle_degrees: np.ndarray, weights: np.ndarray) -> float:
    """Shannon entropy (bits) of a magnitude-weighted direction histogram."""
    histogram, _ = np.histogram(
        angle_degrees, bins=_DIRECTION_HISTOGRAM_BINS, range=(0.0, 360.0), weights=weights
    )
    total_weight = histogram.sum()
    probabilities = histogram[histogram > 0] / total_weight
    return float(-np.sum(probabilities * np.log2(probabilities)))
