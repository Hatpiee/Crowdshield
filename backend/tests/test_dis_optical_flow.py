import ast
from pathlib import Path

import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.dis_optical_flow import DISOpticalFlowAdapter, UnsupportedDISPresetError
from app.pipeline.frame import Frame

PIPELINE_DIR = Path(__file__).resolve().parents[1] / "app" / "pipeline"
FORBIDDEN_MODULE_COMPONENTS = {"detector", "yolo_detector", "tracker", "bytetrack_adapter"}


def _imported_module_components(file_path: Path) -> set[str]:
    """All dotted-path components referenced by import/from-import
    statements in a source file — via ast, not string-matching, so this
    can't be fooled by e.g. a substring appearing in a comment."""
    tree = ast.parse(file_path.read_text(encoding="utf-8"), filename=str(file_path))
    components: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                components.update(alias.name.split("."))
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                components.update(node.module.split("."))
    return components


def test_optical_flow_modules_have_no_detection_or_tracking_dependency():
    # Constitutional independence requirement (§11's FAILURE MODE clause):
    # detection/tracking failure must never be able to take down optical
    # flow, and vice versa. Verified at the source level, not just asserted
    # in a comment.
    for filename in ("optical_flow.py", "dis_optical_flow.py"):
        components = _imported_module_components(PIPELINE_DIR / filename)
        overlap = components & FORBIDDEN_MODULE_COMPONENTS
        assert not overlap, f"{filename} imports forbidden module(s): {overlap}"


def _textured_frame(frame_number: int, fps: float, shift_x: int = 0) -> Frame:
    # A random-noise texture (NOT a flat color — DIS needs texture to find
    # correspondence), optionally shifted by a known pixel offset. The same
    # seed is used for both frames so the only difference is the shift.
    rng = np.random.default_rng(42)
    base = rng.integers(0, 256, size=(120, 160), dtype=np.uint8)
    shifted = np.roll(base, shift_x, axis=1)
    image = np.stack([shifted] * 3, axis=-1).astype(np.uint8)  # fake BGR, all channels equal
    return Frame(
        frame_number=frame_number,
        timestamp_seconds=frame_number / fps,
        image=image,
        width=160,
        height=120,
    )


def test_known_synthetic_translation_matches_expected_shift():
    # Verified empirically before writing this assertion (see phase report):
    # a 5px rightward shift of this exact texture produces a DIS-measured
    # mean magnitude of ~5.06px and a magnitude-weighted circular mean
    # direction of ~1.5 degrees (0 degrees = pure +x). DIS is an iterative/
    # approximate algorithm, so exact equality is not expected — generous
    # tolerances below reflect that, not a loose test.
    prev = _textured_frame(0, fps=30.0, shift_x=0)
    curr = _textured_frame(1, fps=30.0, shift_x=5)

    adapter = DISOpticalFlowAdapter()
    result = adapter.compute(prev, curr)

    assert result.mean_velocity == pytest.approx(5.0, abs=1.5)
    assert result.dominant_direction_degrees is not None
    # Circular distance from 0 degrees (handles the 359-vs-1 wraparound).
    circular_distance = min(
        result.dominant_direction_degrees, 360.0 - result.dominant_direction_degrees
    )
    assert circular_distance < 15.0
    assert result.preset_used == settings.DIS_PRESET
    assert result.frame_number == curr.frame_number
    assert result.prev_frame_number == prev.frame_number


def test_zero_motion_between_identical_frames():
    frame_a = _textured_frame(0, fps=30.0, shift_x=0)
    frame_b = _textured_frame(1, fps=30.0, shift_x=0)  # identical texture, no shift

    adapter = DISOpticalFlowAdapter()
    result = adapter.compute(frame_a, frame_b)

    assert result.mean_velocity == pytest.approx(0.0, abs=0.05)


def test_no_signal_gives_none_not_fabricated_zero(monkeypatch):
    # Raise the noise floor above any real motion this pair could produce,
    # forcing the "zero pixels clear the floor" branch — confirms direction/
    # entropy are None (never fabricated as 0) while mean_velocity/variance
    # correctly ARE 0.0 (zero motion is a real, valid measurement).
    monkeypatch.setattr(settings, "MOTION_MAGNITUDE_NOISE_FLOOR", 1000.0)

    frame_a = _textured_frame(0, fps=30.0, shift_x=0)
    frame_b = _textured_frame(1, fps=30.0, shift_x=0)

    adapter = DISOpticalFlowAdapter()
    result = adapter.compute(frame_a, frame_b)

    assert result.dominant_direction_degrees is None
    assert result.directional_entropy is None
    assert result.mean_velocity == 0.0
    assert result.velocity_variance == 0.0
    assert result.noise_floor_used == 1000.0


def test_mismatched_dimensions_raises():
    small = Frame(
        frame_number=0,
        timestamp_seconds=0.0,
        image=np.zeros((100, 100, 3), dtype=np.uint8),
        width=100,
        height=100,
    )
    large = Frame(
        frame_number=1,
        timestamp_seconds=1 / 30.0,
        image=np.zeros((200, 200, 3), dtype=np.uint8),
        width=200,
        height=200,
    )

    adapter = DISOpticalFlowAdapter()
    with pytest.raises(ValueError):
        adapter.compute(small, large)


def test_preset_configurability_reflected_in_result(monkeypatch):
    monkeypatch.setattr(settings, "DIS_PRESET", "medium")
    adapter = DISOpticalFlowAdapter()

    frame_a = _textured_frame(0, fps=30.0, shift_x=0)
    frame_b = _textured_frame(1, fps=30.0, shift_x=2)
    result = adapter.compute(frame_a, frame_b)

    assert result.preset_used == "medium"
    assert adapter._dis is not None  # constructed successfully with the real constant


def test_unsupported_preset_raises(monkeypatch):
    monkeypatch.setattr(settings, "DIS_PRESET", "not-a-real-preset")

    with pytest.raises(UnsupportedDISPresetError):
        DISOpticalFlowAdapter()
