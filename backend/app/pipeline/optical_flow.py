from abc import ABC, abstractmethod

from app.pipeline.frame import Frame
from app.pipeline.motion import MotionResult


class OpticalFlow(ABC):
    """Abstract contract for a dense optical flow computation (master spec
    §28's module-contract pattern).

    DISOpticalFlowAdapter (wrapping cv2.DISOpticalFlow) is the only
    concrete implementation in this codebase today. DIS (Dense Inverse
    Search) is the frozen algorithm per §5/§11/§40 — do not substitute
    TV-L1, RAFT, or Farnebäck.

    INDEPENDENCE IS CONSTITUTIONAL (§11's FAILURE MODE clause): "detection/
    tracking failure does not automatically imply motion-analysis failure,
    and vice versa -- the two channels fail independently." This module and
    its concrete implementations MUST have zero import dependency on
    detector.py, yolo_detector.py, tracker.py, or bytetrack_adapter.py
    (Phases 6-7) — compute() operates on raw Frame objects only, never on
    DetectionResult or TrackingResult.

    STATEFULNESS — DISTINCT FROM Tracker (Phase 7): the underlying DIS
    algorithm object should be constructed once per OpticalFlow instance
    (same "load once" performance principle as Phase 6's YOLO model
    loading), but compute() itself does NOT accumulate cross-call
    history/state — each call's output depends only on the two frames
    passed to that specific call. Unlike Tracker, there is no trajectory
    state to leak between calls, so there is no "never reuse across two
    videos" restriction here; an OpticalFlow instance may safely be reused
    across unrelated frame pairs or even unrelated videos.
    """

    @abstractmethod
    def compute(self, prev_frame: Frame, curr_frame: Frame) -> MotionResult: ...

    @staticmethod
    def _validate_matching_dimensions(prev_frame: Frame, curr_frame: Frame) -> None:
        """Shared validation for concrete compute() implementations to call
        first — raises a clear error rather than letting a shape mismatch
        fail unpredictably deep inside an OpenCV call."""
        if prev_frame.width != curr_frame.width or prev_frame.height != curr_frame.height:
            raise ValueError(
                "prev_frame and curr_frame must have matching dimensions, "
                f"got {prev_frame.width}x{prev_frame.height} vs "
                f"{curr_frame.width}x{curr_frame.height}"
            )
