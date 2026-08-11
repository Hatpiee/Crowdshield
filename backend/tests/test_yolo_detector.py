import numpy as np
import pytest

from app.core.config import settings
from app.pipeline.frame import Frame
from app.pipeline.yolo_detector import (
    UnsupportedDetectorRuntimeError,
    YOLO11nDetector,
)

# NOTE on the optional positive-detection fixture (Step 8): skipped here.
# We could not confidently source a small (<200KB), clearly CC0/public
# domain-licensed image containing a real visible person from within this
# session. Positive-detection validation instead happens against the
# developer's own real footage via scripts/preview_detection.py, which has
# no licensing concern since it's the developer's own video — see this
# phase's Definition of Done report.


@pytest.fixture(scope="session")
def detector() -> YOLO11nDetector:
    # Real weights, real inference — not mocked. This project's principle is
    # "never claim something works unless it has actually been tested," so
    # even this negative (blank-image) case uses genuine YOLO11n inference.
    # Session-scoped so the comparatively expensive model load happens once
    # for the whole test run, not once per test.
    return YOLO11nDetector()


def _blank_frame() -> Frame:
    # Plain solid-color numpy array, no external file needed — no people in
    # it, so this is the negative case (zero detections expected).
    image = np.zeros((480, 640, 3), dtype=np.uint8)
    return Frame(
        frame_number=7, timestamp_seconds=0.7, image=image, width=640, height=480
    )


def test_blank_frame_yields_zero_detections_no_error(detector):
    result = detector.detect(_blank_frame())

    assert result.detections == []


def test_result_passes_through_frame_number_and_timestamp(detector):
    frame = _blank_frame()
    result = detector.detect(frame)

    assert result.frame_number == frame.frame_number
    assert result.timestamp_seconds == pytest.approx(frame.timestamp_seconds)


def test_result_reports_model_name_and_confidence_threshold(detector):
    result = detector.detect(_blank_frame())

    assert result.model_name == settings.DETECTOR_MODEL
    assert result.confidence_threshold_used == pytest.approx(
        settings.DETECTOR_CONFIDENCE_THRESHOLD
    )


def test_unsupported_detector_runtime_raises(monkeypatch):
    monkeypatch.setattr(settings, "DETECTOR_RUNTIME", "openvino")

    with pytest.raises(UnsupportedDetectorRuntimeError):
        YOLO11nDetector()
