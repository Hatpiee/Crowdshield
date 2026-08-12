"""VisionModel interface — roadmap Phase 14, master spec §28's frozen
component contract: `VisionModel.analyze(vision_input) -> ...`. Refined per
this project's established pattern of clarifying a contract's real wrapped-
list shape (already applied to TrackingResult in Phase 7 and MotionResult
in Phase 8): §16 confirms the real output shape is "VisionObservations[]",
a LIST — so `analyze()` returns a `VisionAnalysisResult` WRAPPING a list of
`VisionObservation`, not a single bare observation.

Deliberately generic enough for a future Qwen2.5-VL-3B adapter (§7/§35's
pilot A/B candidate) to be added later with NO change to any caller —
callers depend only on this ABC, never on `MiniCPMVisionModel` directly.
That adapter is explicitly NOT built in this phase; it is deferred to a
dedicated future validation task.
"""

from abc import ABC, abstractmethod

from app.pipeline.vision_observation import VisionAnalysisResult, VisionInput


class VisionModel(ABC):
    @abstractmethod
    def analyze(self, vision_input: VisionInput) -> VisionAnalysisResult:
        ...
