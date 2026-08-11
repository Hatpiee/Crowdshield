"""YOLO11n person detector (PyTorch/ultralytics backend).

The model is loaded exactly ONCE per detector instance, in __init__ —
model loading has significant overhead, and reloading it per detect()
call would make the system unusable on a per-frame pipeline.

CPU inference is forced explicitly (device="cpu") regardless of what
hardware happens to be available on the machine running this code.
Master spec §5: "Every model at every layer was selected under a
strict CPU-only constraint" — this must produce honest, representative
CPU performance numbers (§33), not opportunistic GPU speedup.

Person-only filtering (COCO class 0) and confidence thresholding use
ultralytics' own built-in `classes=` and `conf=` inference parameters
rather than manual post-filtering, since `conf=` also correctly ties
into ultralytics' internal NMS behavior.

OpenVINO IR + INT8 quantization (§5, §40) is deliberately NOT
implemented here — deferred to roadmap Phase 23 (CPU optimization).
This class exists behind the Detector ABC specifically so a future
OpenVINODetector can be swapped in later without touching any
downstream consumer.
"""

from app.core.config import REPO_ROOT, settings
from app.pipeline.box_to_point import collapse_box_to_point
from app.pipeline.detection import Detection, DetectionResult
from app.pipeline.detector import Detector
from app.pipeline.frame import Frame

MODEL_STORAGE_DIR = "storage/models"

# Maps the config-level model identifier (settings.DETECTOR_MODEL) to its
# ultralytics weights filename — never hardcode "yolo11n.pt" directly and
# bypass this mapping (§32's no-hardcoded-model-names rule).
_MODEL_WEIGHTS_FILENAMES = {
    "yolo11n": "yolo11n.pt",
}


class UnsupportedDetectorRuntimeError(Exception):
    pass


class UnknownDetectorModelError(Exception):
    pass


class YOLO11nDetector(Detector):
    def __init__(self) -> None:
        if settings.DETECTOR_RUNTIME != "pytorch":
            raise UnsupportedDetectorRuntimeError(
                f"DETECTOR_RUNTIME={settings.DETECTOR_RUNTIME!r} is not "
                "supported. Only 'pytorch' is implemented; OpenVINO support "
                "is deferred to roadmap Phase 23 (CPU optimization)."
            )

        weights_filename = _MODEL_WEIGHTS_FILENAMES.get(settings.DETECTOR_MODEL)
        if weights_filename is None:
            raise UnknownDetectorModelError(
                f"DETECTOR_MODEL={settings.DETECTOR_MODEL!r} has no known "
                f"weights file mapping. Known models: "
                f"{sorted(_MODEL_WEIGHTS_FILENAMES)}"
            )

        # Resolved absolutely against REPO_ROOT (same fix as
        # VIDEO_STORAGE_PATH in earlier phases) so this is correct
        # regardless of the process's current working directory.
        model_dir = REPO_ROOT / MODEL_STORAGE_DIR
        model_dir.mkdir(parents=True, exist_ok=True)
        weights_path = model_dir / weights_filename

        # Imported lazily so importing this module (e.g. for the Detector
        # ABC or dataclasses elsewhere) never pays ultralytics/torch's
        # substantial import cost unless a real detector is constructed.
        from ultralytics import YOLO

        # ultralytics auto-downloads the weights file to this exact path on
        # first use if it isn't already present locally (requires internet
        # access on first run only; subsequent runs use the local cached
        # file — see this phase's Definition of Done report for the
        # confirmed download path).
        self._model = YOLO(str(weights_path))
        self._model_name = settings.DETECTOR_MODEL
        self._confidence_threshold = settings.DETECTOR_CONFIDENCE_THRESHOLD

    def detect(self, frame: Frame) -> DetectionResult:
        # frame.image is BGR, not RGB (see Frame's docstring, Phase 5).
        # Ultralytics expects BGR-order numpy arrays natively when given a
        # raw array source, so no color conversion is needed here — this
        # assumption is easy to get backwards, hence the explicit callout.
        results = self._model.predict(
            source=frame.image,
            classes=[0],
            conf=self._confidence_threshold,
            device="cpu",
            verbose=False,
        )

        detections: list[Detection] = []
        for result in results:
            for box in result.boxes:
                x1, y1, x2, y2 = (float(v) for v in box.xyxy[0].tolist())
                confidence = float(box.conf[0])
                point, local_scale = collapse_box_to_point((x1, y1, x2, y2))
                detections.append(
                    Detection(
                        point=point,
                        local_scale=local_scale,
                        confidence=confidence,
                        raw_box=(x1, y1, x2, y2),
                    )
                )

        return DetectionResult(
            frame_number=frame.frame_number,
            timestamp_seconds=frame.timestamp_seconds,
            detections=detections,
            model_name=self._model_name,
            confidence_threshold_used=self._confidence_threshold,
        )
