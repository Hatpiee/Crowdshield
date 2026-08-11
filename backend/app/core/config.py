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
    # Phase 9: pixels per square grid cell edge for the shared spatial grid
    # that Density and Flow are both computed on (so Crowd Pressure can
    # combine them pointwise without interpolation). Grid rows/cols are
    # derived from frame width/height divided by this value.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (same category as DIS_PRESET and
    # MOTION_MAGNITUDE_NOISE_FLOOR above — logged in DECISIONS.md): 40,
    # not calibrated against any real venue's physical scale (no camera
    # calibration exists in this project — see DECISIONS.md's "Known
    # Structural Limitation: Pixel-Space vs. Real-World Units" section).
    CROWD_GRID_CELL_SIZE_PX: int = 40
    # Phase 10 (Congestion, decision #2): a cell counts as "dense" once its
    # DensityField.grid value (people/cell) reaches this. Density ALONE
    # never triggers congestion (frozen decision) — see
    # FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC below, required in
    # combination.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (same category as CROWD_GRID_CELL_SIZE_PX
    # above — logged in DECISIONS.md): 0.1, informed by real observed values
    # from Phase 9's preview script re-run against people_clip.mp4 — density
    # at "occupied" cells (density > 0.01) had p90=0.108 across a 149-frame
    # run, and stable, non-degraded, multi-track frames (6-10 confirmed
    # tracks) typically peaked at max_density ~0.11-0.18. 0.1 sits just
    # below that observed range, not calibrated against any real venue.
    DENSITY_CONGESTION_THRESHOLD: float = 0.1
    # Phase 10 (Congestion, decision #2): a cell counts as "low flow" (the
    # OTHER required half of congestion) once its FlowGridField
    # grid_mean_velocity magnitude (true pixels/second, per Phase 9) drops
    # to or below this.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 40.0,
    # informed by the same real preview re-run — per-cell speed at
    # "occupied" cells had p25=28.0 px/s, and the whole-grid speed
    # distribution (all cells, all frames) had p25=37.6 px/s. 40.0 sits just
    # above both lower-quartile values, intended to capture the bottom
    # ~quarter of observed motion as "notably slow," not calibrated against
    # any real venue's physical scale.
    FLOW_MAGNITUDE_CONGESTION_THRESHOLD_PX_PER_SEC: float = 40.0
    # Phase 10 (Bottleneck, decision #3): how many recent FlowGridField
    # snapshots the rolling window keeps for tracer advection. Not a
    # pixel-space quantity — this is a frame-count/temporal-horizon choice,
    # not a units-disclosure-affected constant.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 30 (~1s at
    # people_clip.mp4's 30fps), the spec's own suggested starting point — no
    # sensitivity analysis performed on how window length affects the
    # bottleneck score's reliability.
    BOTTLENECK_WINDOW_FRAMES: int = 30
    # Phase 10 (Reverse Flow, decision #4): exponential-moving-average decay
    # rate for each grid cell's learned baseline direction (higher = faster
    # adaptation to recent direction, lower = more stable/slower-adapting
    # baseline). Angle-based, not a pixel-space quantity.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 0.1, the
    # spec's own suggested default — not tuned against real footage.
    REVERSE_FLOW_BASELINE_EMA_ALPHA: float = 0.1
    # Phase 10 (Reverse Flow, decision #4): minimum number of motion
    # observations a cell must accumulate before its baseline is trusted
    # enough to check for reverse flow at all.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 15, the
    # spec's own suggested default.
    REVERSE_FLOW_MIN_BASELINE_OBSERVATIONS: int = 15
    # Phase 10 (Reverse Flow, decision #4): angular deviation (degrees)
    # between a cell's current-frame direction and its established baseline
    # beyond which that single frame counts as "locally reversed." Angle-
    # based — unit-agnostic, no pixel-space calibration concern.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 120.0, the
    # spec's own suggested default.
    REVERSE_FLOW_DEVIATION_THRESHOLD_DEGREES: float = 120.0
    # Phase 10 (Reverse Flow, decision #4): size of the rolling boolean
    # window (in frames) of "locally reversed" flags kept per cell for the
    # temporal-persistence check.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 10, the
    # spec's own suggested default.
    REVERSE_FLOW_PERSISTENCE_WINDOW_FRAMES: int = 10
    # Phase 10 (Reverse Flow, decision #4): minimum number of "locally
    # reversed" frames within the persistence window required before
    # is_reverse_flow is actually set True for a cell.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 6, the
    # spec's own suggested default.
    REVERSE_FLOW_PERSISTENCE_MIN_COUNT: int = 6
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
