"""Acute-Hazard Trigger Phase: AcuteHazardDetector tests. Follows
test_bottleneck.py's/test_reverse_flow.py's exact established fixture-
building conventions (fixed synthetic geometry, hand-built FlowGridField/
MotionResult/DetectionResult objects, thresholds monkeypatched small so
baseline windows are cheap to satisfy).

Per the developer's own explicit choice (documented in
acute_hazard_detector.py's module docstring and DECISIONS.md): the real
blast-video regression fixture was found to be byte-identical to
people_clip.mp4 — no real blast footage exists in this repo yet. Every
case below is therefore SYNTHETIC, clearly labeled as such — none of these
prove real-world calibration, only the deterministic fusion logic itself.
"""

import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.acute_hazard_detector import (
    AcuteHazardDetector,
    compute_scene_change_score,
)
from app.pipeline.crowd_grid import CrowdGrid
from app.pipeline.detection import Detection, DetectionResult, Point
from app.pipeline.flow_field import FlowGridField
from app.pipeline.frame import Frame
from app.pipeline.motion import MotionResult

FRAME_WIDTH = 320
FRAME_HEIGHT = 240
ROWS, COLS = 6, 8


@pytest.fixture(autouse=True)
def _fast_config(monkeypatch):
    # Same "monkeypatch thresholds small" discipline as
    # test_reverse_flow.py's own autouse fixture — cheap baseline warm-up
    # for tests, never the real production defaults.
    monkeypatch.setattr(settings, "ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS", 5)
    monkeypatch.setattr(settings, "ACUTE_HAZARD_BASELINE_EMA_ALPHA", 0.3)
    monkeypatch.setattr(settings, "ACUTE_HAZARD_ZSCORE_THRESHOLD", 3.0)
    monkeypatch.setattr(settings, "ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS", 2)
    monkeypatch.setattr(settings, "ACUTE_HAZARD_COOLDOWN_SECONDS", 1.0)


def _grid() -> CrowdGrid:
    return CrowdGrid(
        rows=ROWS, cols=COLS,
        cell_width_px=FRAME_WIDTH / COLS, cell_height_px=FRAME_HEIGHT / ROWS,
        frame_width=FRAME_WIDTH, frame_height=FRAME_HEIGHT,
    )


def _flow_field(
    frame_number: int, timestamp_seconds: float,
    mean_speed: float = 5.0, divergence: float = 0.0,
) -> FlowGridField:
    velocity = np.full((ROWS, COLS, 2), mean_speed / np.sqrt(2))
    return FlowGridField(
        frame_number=frame_number, timestamp_seconds=timestamp_seconds,
        grid_mean_velocity=velocity,
        grid_velocity_variance=np.zeros((ROWS, COLS)),
        grid_divergence=np.full((ROWS, COLS), divergence),
        grid_curl=np.zeros((ROWS, COLS)),
        source_motion_frame_number=frame_number,
    )


def _motion_result(frame_number: int, timestamp_seconds: float, mean_velocity: float) -> MotionResult:
    return MotionResult(
        frame_number=frame_number, prev_frame_number=frame_number - 1,
        timestamp_seconds=timestamp_seconds,
        flow_field=np.zeros((FRAME_HEIGHT, FRAME_WIDTH, 2)),
        mean_velocity=mean_velocity, velocity_variance=1.0,
        dominant_direction_degrees=0.0, directional_entropy=1.0,
        preset_used="fast", noise_floor_used=0.5,
    )


def _detection_result(frame_number: int, count: int) -> DetectionResult:
    return DetectionResult(
        frame_number=frame_number, timestamp_seconds=frame_number / 30.0,
        detections=[Detection(point=Point(x=1.0, y=1.0), local_scale=1.0, confidence=0.9)] * count,
        model_name="test", confidence_threshold_used=0.25,
    )


def _frame(frame_number: int, timestamp_seconds: float, fill_value: int = 100) -> Frame:
    image = np.full((FRAME_HEIGHT, FRAME_WIDTH, 3), fill_value, dtype=np.uint8)
    return Frame(
        frame_number=frame_number, timestamp_seconds=timestamp_seconds, image=image,
        width=FRAME_WIDTH, height=FRAME_HEIGHT,
    )


def _feed_calm_baseline(detector: AcuteHazardDetector, num_frames: int = 10):
    """Feeds `num_frames` of uniform, unremarkable "calm" signal readings —
    same value every frame, no motion/appearance change, no detection
    fluctuation — so the detector's baseline is genuinely established
    without ever being anomalous itself."""
    last_signal = None
    for i in range(1, num_frames + 1):
        signal = detector.update(
            motion_result=_motion_result(i, i / 30.0, mean_velocity=5.0),
            flow_grid_field=_flow_field(i, i / 30.0, mean_speed=5.0, divergence=0.0),
            detection_result=_detection_result(i, count=10),
            prev_frame=_frame(i - 1, (i - 1) / 30.0, fill_value=100),
            curr_frame=_frame(i, i / 30.0, fill_value=100),
        )
        last_signal = signal
    return last_signal


# ---------------------------------------------------------------------------
# Warm-up / baseline establishment
# ---------------------------------------------------------------------------


def test_never_flags_before_baseline_established():
    detector = AcuteHazardDetector(_grid())
    for i in range(1, settings.ACUTE_HAZARD_MIN_BASELINE_OBSERVATIONS):
        signal = detector.update(
            motion_result=_motion_result(i, i / 30.0, mean_velocity=5.0),
            flow_grid_field=_flow_field(i, i / 30.0),
            detection_result=_detection_result(i, count=10),
            prev_frame=_frame(i - 1, (i - 1) / 30.0),
            curr_frame=_frame(i, i / 30.0),
        )
        assert signal.is_acute_hazard is False
        assert signal.baseline_established is False


def test_calm_baseline_never_flags():
    detector = AcuteHazardDetector(_grid())
    signal = _feed_calm_baseline(detector, num_frames=20)
    assert signal.baseline_established is True
    assert signal.is_acute_hazard is False
    assert signal.corroborating_signals == []


# ---------------------------------------------------------------------------
# Quorum requirement — single-signal spike is NOT sufficient
# ---------------------------------------------------------------------------


def test_single_signal_spike_alone_does_not_flag():
    detector = AcuteHazardDetector(_grid())
    _feed_calm_baseline(detector, num_frames=10)

    # Only motion_energy spikes; everything else stays at its calm baseline
    # value — below the ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS=2 quorum.
    signal = detector.update(
        motion_result=_motion_result(11, 11 / 30.0, mean_velocity=500.0),
        flow_grid_field=_flow_field(11, 11 / 30.0, mean_speed=5.0, divergence=0.0),
        detection_result=_detection_result(11, count=10),
        prev_frame=_frame(10, 10 / 30.0, fill_value=100),
        curr_frame=_frame(11, 11 / 30.0, fill_value=100),
    )
    assert signal.is_acute_hazard is False
    assert "motion_energy" in signal.corroborating_signals
    assert len(signal.corroborating_signals) < settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS


# ---------------------------------------------------------------------------
# Genuine multi-signal spike — quorum met, detector flags
# ---------------------------------------------------------------------------


def test_multi_signal_spike_flags_is_acute_hazard():
    detector = AcuteHazardDetector(_grid())
    _feed_calm_baseline(detector, num_frames=10)

    # motion_energy, flow_divergence, AND scene_change all spike together
    # (a genuine abrupt-event-like SYNTHETIC signature) — detection count
    # stays put. 3 of 4 signals corroborate, clearing the quorum of 2.
    signal = detector.update(
        motion_result=_motion_result(11, 11 / 30.0, mean_velocity=500.0),
        flow_grid_field=_flow_field(11, 11 / 30.0, mean_speed=500.0, divergence=50.0),
        detection_result=_detection_result(11, count=10),
        prev_frame=_frame(10, 10 / 30.0, fill_value=100),
        curr_frame=_frame(11, 11 / 30.0, fill_value=250),
    )
    assert signal.is_acute_hazard is True
    assert "motion_energy" in signal.corroborating_signals
    assert "flow_divergence" in signal.corroborating_signals
    assert "scene_change" in signal.corroborating_signals
    assert len(signal.corroborating_signals) >= settings.ACUTE_HAZARD_MIN_CORROBORATING_SIGNALS
    # localization_grid must reflect the real per-cell divergence magnitude,
    # never fabricated — a uniform spike produces a uniform localization
    # grid here, but it must be genuinely computed, not a placeholder.
    assert signal.localization_grid.shape == (ROWS, COLS)
    assert np.all(signal.localization_grid == pytest.approx(50.0))


# ---------------------------------------------------------------------------
# Camera-motion/global-jitter mitigation (decision E's own requirement) —
# uniform whole-frame motion must NOT reach quorum
# ---------------------------------------------------------------------------


def test_uniform_camera_pan_does_not_flag_synthetic():
    """SYNTHETIC camera-pan simulation: a uniform velocity field (every
    cell moving together, e.g. the whole camera panned) spikes raw motion
    magnitude and scene appearance together, but produces near-ZERO
    divergence (uniform motion has no spatial gradient) and no detection-
    count discontinuity — this must NOT reach the 2-signal quorum, proving
    the camera-motion mitigation acute_hazard_detector.py's own module
    docstring describes."""
    detector = AcuteHazardDetector(_grid())
    _feed_calm_baseline(detector, num_frames=10)

    signal = detector.update(
        motion_result=_motion_result(11, 11 / 30.0, mean_velocity=500.0),
        # Large uniform velocity -> near-zero divergence (all cells move
        # identically, so np.gradient of a constant field is ~0).
        flow_grid_field=_flow_field(11, 11 / 30.0, mean_speed=500.0, divergence=0.0),
        detection_result=_detection_result(11, count=10),
        prev_frame=_frame(10, 10 / 30.0, fill_value=100),
        curr_frame=_frame(11, 11 / 30.0, fill_value=250),
    )
    assert signal.is_acute_hazard is False
    assert "flow_divergence" not in signal.corroborating_signals
    assert "detection_count_delta" not in signal.corroborating_signals


# ---------------------------------------------------------------------------
# Detection-count discontinuity contributes correctly
# ---------------------------------------------------------------------------


def test_detection_count_collapse_contributes_to_corroboration():
    detector = AcuteHazardDetector(_grid())
    _feed_calm_baseline(detector, num_frames=10)

    # Detection count collapses (people obscured/scattered) alongside a
    # genuine motion spike.
    signal = detector.update(
        motion_result=_motion_result(11, 11 / 30.0, mean_velocity=500.0),
        flow_grid_field=_flow_field(11, 11 / 30.0, mean_speed=500.0, divergence=50.0),
        detection_result=_detection_result(11, count=0),
        prev_frame=_frame(10, 10 / 30.0, fill_value=100),
        curr_frame=_frame(11, 11 / 30.0, fill_value=100),
    )
    assert "detection_count_delta" in signal.corroborating_signals
    assert signal.is_acute_hazard is True


# ---------------------------------------------------------------------------
# Cooldown suppresses re-firing
# ---------------------------------------------------------------------------


def test_cooldown_suppresses_immediate_refire():
    detector = AcuteHazardDetector(_grid())
    _feed_calm_baseline(detector, num_frames=10)

    first = detector.update(
        motion_result=_motion_result(11, 11 / 30.0, mean_velocity=500.0),
        flow_grid_field=_flow_field(11, 11 / 30.0, mean_speed=500.0, divergence=50.0),
        detection_result=_detection_result(11, count=0),
        prev_frame=_frame(10, 10 / 30.0, fill_value=100),
        curr_frame=_frame(11, 11 / 30.0, fill_value=250),
    )
    assert first.is_acute_hazard is True

    # One frame later (well within ACUTE_HAZARD_COOLDOWN_SECONDS=1.0,
    # monkeypatched by _fast_config) — same spike conditions, but the
    # cooldown must suppress firing again.
    second = detector.update(
        motion_result=_motion_result(12, 12 / 30.0, mean_velocity=500.0),
        flow_grid_field=_flow_field(12, 12 / 30.0, mean_speed=500.0, divergence=50.0),
        detection_result=_detection_result(12, count=0),
        prev_frame=_frame(11, 11 / 30.0, fill_value=250),
        curr_frame=_frame(12, 12 / 30.0, fill_value=250),
    )
    assert second.is_acute_hazard is False


# ---------------------------------------------------------------------------
# Two detector instances never share state
# ---------------------------------------------------------------------------


def test_two_detectors_do_not_share_state():
    detector_a = AcuteHazardDetector(_grid())
    detector_b = AcuteHazardDetector(_grid())

    # Warm up detector_a only.
    _feed_calm_baseline(detector_a, num_frames=10)

    signal_a = detector_a.update(
        motion_result=_motion_result(11, 11 / 30.0, mean_velocity=5.0),
        flow_grid_field=_flow_field(11, 11 / 30.0, mean_speed=5.0, divergence=0.0),
        detection_result=_detection_result(11, count=10),
        prev_frame=_frame(10, 10 / 30.0, fill_value=100),
        curr_frame=_frame(11, 11 / 30.0, fill_value=100),
    )
    signal_b = detector_b.update(
        motion_result=_motion_result(1, 1 / 30.0, mean_velocity=5.0),
        flow_grid_field=_flow_field(1, 1 / 30.0, mean_speed=5.0, divergence=0.0),
        detection_result=_detection_result(1, count=10),
        prev_frame=_frame(0, 0.0, fill_value=100),
        curr_frame=_frame(1, 1 / 30.0, fill_value=100),
    )
    assert signal_a.baseline_established is True
    assert signal_b.baseline_established is False, (
        "detector_b's own baseline must be independent of detector_a's — "
        "warming up one instance must never establish the other's baseline"
    )


# ---------------------------------------------------------------------------
# compute_scene_change_score — pure function, direct checks
# ---------------------------------------------------------------------------


def test_scene_change_score_zero_for_identical_frames():
    frame = _frame(0, 0.0, fill_value=100)
    assert compute_scene_change_score(frame, frame) == pytest.approx(0.0)


def test_scene_change_score_positive_for_different_frames():
    prev = _frame(0, 0.0, fill_value=0)
    curr = _frame(1, 1 / 30.0, fill_value=255)
    score = compute_scene_change_score(prev, curr)
    assert score == pytest.approx(1.0, abs=0.01)
