from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Resolved absolutely so paths derived from it are correct regardless of the
# process's current working directory (uvicorn, alembic, pytest, and scripts/
# are all invoked from different cwds).
REPO_ROOT = Path(__file__).resolve().parents[3]
_ENV_FILE = REPO_ROOT / ".env"


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg2://user:password@localhost:5432/crowdshield"
    DATABASE_URL_TEST: str = "postgresql+psycopg2://user:password@localhost:5432/crowdshield_test"
    AUTH_SECRET: str = "changeme"
    JWT_SECRET_KEY: str = "changeme"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    VIDEO_STORAGE_PATH: str = "storage/videos"
    MAX_UPLOAD_SIZE_MB: int = 2048
    LOG_LEVEL: str = "INFO"
    # Phase 6: corrected from the Phase 1 placeholder values ("yolov8n" /
    # "cpu") to reflect the frozen decisions actually built — YOLO11n is the
    # frozen detector (master spec §5/§9/§12/§40), and DETECTOR_RUNTIME now
    # means "which inference backend" (pytorch today; openvino deferred to
    # roadmap Phase 23), not "which device" — CPU is forced unconditionally
    # in YOLO11nDetector regardless of this setting.
    DETECTOR_MODEL: str = "yolo11n"
    DETECTOR_RUNTIME: str = "pytorch"
    DETECTOR_CONFIDENCE_THRESHOLD: float = 0.25
    # Phase 7: how many of a track's most recent positions are retained in
    # memory (bounded, to avoid unbounded growth over a long video).
    TRACK_HISTORY_LENGTH: int = 30
    # Phase 7: verified real constructor params of trackers.ByteTrackTracker
    # (see backend/app/pipeline/bytetrack_adapter.py docstring for the full
    # inspection notes). All other ByteTrackTracker params are left at their
    # library defaults.
    TRACKER_LOST_TRACK_BUFFER: int = 30
    TRACKER_MINIMUM_CONSECUTIVE_FRAMES: int = 2
    # Minimum detection confidence to CREATE a new track (distinct from
    # DETECTOR_CONFIDENCE_THRESHOLD, which gates what counts as a detection
    # at all). The library's own general-purpose default is 0.7, but
    # real-footage testing found that most real-world detections from
    # YOLO11nDetector (tuned to DETECTOR_CONFIDENCE_THRESHOLD=0.25 for
    # recall) never clear 0.7, so almost nothing gets a confirmed track_id
    # at that default on dense-crowd footage.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (same category as Phase 6's
    # box-to-point collapse constants — not empirically tuned against real
    # crowd footage, a candidate for recalibration during Sprint-0
    # validation, §35, exactly like the master spec's own Crowd Pressure
    # thresholds are documented as open for recalibration): 0.3, chosen to
    # sit just above DETECTOR_CONFIDENCE_THRESHOLD's 0.25 floor so most
    # real person detections have a realistic chance of being confirmed as
    # tracks. Logged in DECISIONS.md.
    TRACKER_TRACK_ACTIVATION_THRESHOLD: float = 0.3
    # Phase 8: which cv2.DISOpticalFlow_create preset to use — "ultrafast",
    # "fast", or "medium" (mapped to the real verified
    # cv2.DISOPTICAL_FLOW_PRESET_* constants in dis_optical_flow.py).
    #
    # UNVALIDATED ENGINEERING JUDGMENT (same category as the box-to-point
    # constants and TRACKER_TRACK_ACTIVATION_THRESHOLD above — logged in
    # DECISIONS.md): "fast", chosen as a balance between quality and CPU
    # cost per §5's own "~21-48 FPS depending on preset" citation. No
    # benchmark has been run on this project's target hardware yet —
    # candidate for Sprint-0 recalibration.
    DIS_PRESET: str = "fast"
    # Phase 8: flow-magnitude floor (pixels) below which a pixel is excluded
    # from this phase's whole-frame summary statistics (mean_velocity,
    # velocity_variance, dominant_direction_degrees, directional_entropy) —
    # NEVER applied to the raw flow_field itself, which stays unfiltered for
    # downstream consumers. Same unvalidated-engineering-judgment status as
    # DIS_PRESET above — logged in DECISIONS.md.
    MOTION_MAGNITUDE_NOISE_FLOOR: float = 0.5
    VLM_MODEL: str = "placeholder-vlm"
    LLM_MODEL: str = "placeholder-llm"
    RISK_ELEVATED_THRESHOLD: float = 0.5
    RISK_CRITICAL_THRESHOLD: float = 0.75
    RISK_INCIDENT_THRESHOLD: float = 0.9
    VLM_COOLDOWN: int = 30
    FALLBACK_ANALYSIS_INTERVAL: int = 60
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")


settings = Settings()
