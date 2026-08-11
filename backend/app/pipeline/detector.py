from abc import ABC, abstractmethod

from app.pipeline.detection import DetectionResult
from app.pipeline.frame import Frame


class Detector(ABC):
    """Abstract contract for a person detector (master spec §28's
    module-contract pattern).

    YOLO11nDetector (PyTorch/ultralytics, plain CPU inference) is the
    only concrete implementation in this codebase today. This interface
    is written generally enough that a future OpenVINODetector (roadmap
    Phase 23, CPU optimization — deferred, not built now) could
    implement it without requiring any change to downstream consumers.
    """

    @abstractmethod
    def detect(self, frame: Frame) -> DetectionResult: ...
