from pathlib import Path

from pydantic import model_validator
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
    # Phase 11 (Risk Score, decision #1): reference max_pressure (pixel-space
    # "people * pixels^2/second^2 per cell", per Phase 9's units disclosure)
    # at which the pressure sub-score saturates at 100. Worst-case-sensitive
    # by design (uses max_pressure, not mean) — a single crushing cell
    # matters even surrounded by calm ones.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 100.0,
    # informed by real observed max_pressure values from Phase 9's preview
    # script re-run against people_clip.mp4 (149 frame-pairs): p25=2.58,
    # median=14.89, p75=41.0, p90=73.4, p95=89.5, max=262.8. 100 sits just
    # above the real p95, so only the top ~5% of observed frames (and any
    # future footage with genuinely worse local crushing) saturate the
    # pressure sub-score at 100 — not calibrated against any real venue.
    PRESSURE_SCORE_REFERENCE_PX: float = 100.0
    # Phase 11 (Risk Score, decision #5): weighted-average weights combining
    # the four sub-scores into risk_score. MUST sum to 1.0 (validated below
    # at settings load time) — when a sub-score is unavailable (only
    # Bottleneck can be, per Phase 10), its weight is proportionally
    # redistributed among the others at RUNTIME (see risk_score.py's
    # _redistribute_weights), not by editing these configured values.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): Pressure
    # gets the largest share (0.5) per §12's own framing of Crowd Pressure as
    # "the single most heavily validated decision in the entire project";
    # the other three split the remainder, with Congestion and Bottleneck
    # weighted equally (0.2 each) and Reverse Flow smallest (0.1) since it's
    # the least-validated mechanism (see DECISIONS.md's "Reverse Flow
    # Mechanism Not Validated for Crowds"). Not empirically tuned.
    RISK_SCORE_WEIGHT_PRESSURE: float = 0.5
    RISK_SCORE_WEIGHT_CONGESTION: float = 0.2
    RISK_SCORE_WEIGHT_BOTTLENECK: float = 0.2
    RISK_SCORE_WEIGHT_REVERSE_FLOW: float = 0.1
    # Phase 11 (Predictive Projection, decision #7): rolling TIME window (not
    # frame count) of recent (timestamp, mean_pressure) pairs used to fit the
    # linear trend.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 10.0
    # seconds, the spec's own suggested default.
    PREDICTIVE_WINDOW_SECONDS: float = 10.0
    # Phase 11 (Predictive Projection, decision #7): how far forward (real
    # seconds) to extrapolate the linear trend. DELIBERATELY NOT the problem
    # statement's "10 minutes before" figure — see predictive_projection.py's
    # module docstring and DECISIONS.md for why conflating the two would be
    # statistically indefensible.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 30.0
    # seconds, the spec's own suggested default.
    PREDICTION_HORIZON_SECONDS: float = 30.0
    # Phase 12 (Heatmaps, decision #3): DensityField.grid value (people/cell)
    # that maps to full-scale ("hottest") heatmap color.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 0.2, informed
    # by real observed values from a fresh Phase 9 preview re-run against
    # people_clip.mp4 (150 frames): restricted to frames where KDE ran at
    # FULL confidence (no too-few-points/singular-covariance degradation,
    # n=35 such frames), max_density ranged 0.078-0.177 (median 0.127,
    # p90=0.163) — i.e. never exceeded ~0.18 under genuine multi-point KDE
    # smoothing on this sparse video. 0.2 sits just above that real observed
    # ceiling. (Across ALL 149 frames including degraded few-point frames,
    # max_density often hit exactly 1.0 — a single track's full weight
    # concentrated into one histogram-fallback cell, per Phase 9's own
    # documented degradation behavior — deliberately NOT used to set this
    # reference, since it reflects a small-N estimation artifact, not
    # genuine crowding.) Not calibrated against any real venue's physical
    # capacity per cell.
    DENSITY_HEATMAP_REFERENCE_COUNT: float = 0.2
    # Phase 12: filesystem path where rendered heatmap JPEGs are stored.
    # Resolved to an absolute path the same way as VIDEO_STORAGE_PATH (Phase
    # 2/3's cwd-relative bug fix) — see heatmap_service.py's get_storage_dir.
    HEATMAP_STORAGE_PATH: str = "storage/heatmaps"
    # Phase 1 placeholder, genuinely activated for the first time in Phase
    # 14. Verified real Ollama model tag (see DECISIONS.md's Ollama-over-
    # raw-bindings and MiniCPM-V 4.6 version-selection entries): MiniCPM-V
    # 4.6 at the Q4_K_M quantization tier — confirmed to exist via
    # ollama.com/library/minicpm-v4.6/tags (13 tags listed, q4_K_M among
    # them, 1.6GB, 256K context) and confirmed actually pulled and runnable
    # in this environment (`ollama list` shows it present).
    VLM_MODEL: str = "minicpm-v4.6:q4_K_M"
    LLM_MODEL: str = "placeholder-llm"
    # Phase 14: base URL of the locally-running Ollama daemon (verified
    # installed and running in this environment — `ollama --version` /
    # `curl http://localhost:11434/api/version` both succeeded). Ollama's
    # own default port; not something this project's own code chose.
    OLLAMA_BASE_URL: str = "http://localhost:11434"
    # Phase 14 (ROI selection, decision #1): the argmax risk-grid cell's
    # own pixel footprint is expanded by this factor (uniformly around its
    # center) before being sent as the VLM's zoomed ROI crop, so the model
    # gets more visual context than one small grid cell alone.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 3.0, a
    # reasonable-looking round multiplier — not tuned against any real
    # "was this crop actually useful to the VLM" evaluation.
    ROI_EXPANSION_FACTOR: float = 3.0
    # Phase 14 (decision #5): if the model's response fails schema
    # validation (defensively re-validated even though Ollama's `format`
    # constraint should already enforce it, per §30), retry this many times
    # before raising VLMResponseValidationError.
    VLM_MAX_RETRIES: int = 2
    # Phase 14 (decision #6): how long to wait for a single Ollama chat
    # call before treating it as unavailable (VLMUnavailableError) — local
    # CPU VLM inference can be slow, so this is deliberately generous
    # compared to a typical web-request timeout.
    VLM_REQUEST_TIMEOUT_SECONDS: float = 60.0
    # Phase 14 (decision #7): low, near-deterministic (but not exactly 0.0,
    # to avoid known degenerate-repetition issues some models exhibit at
    # exactly 0) sampling temperature for schema-constrained structured
    # output, per Ollama's own documented recommendation for structured
    # outputs.
    VLM_TEMPERATURE: float = 0.15
    # Phase 13 (Risk State, Resolution 1 + decisions #1-#4): these three
    # were Phase 1 PLACEHOLDER values on a 0-1 scale, never actually
    # consumed by any code until this phase. RiskStateMachine classifies
    # Phase 11's `risk_score` (0-100, NOT raw pixel-space Crowd Pressure —
    # see risk_state.py's Resolution 1), so the values below are on THAT
    # 0-100 scale, not the old 0-1 placeholders.
    #
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): informed by
    # a fresh real preview re-run of scripts/preview_risk_score_projection.py
    # against people_clip.mp4 (149 frame-pairs) — risk_score distribution:
    # p25=4.32, median=10.08, p75=22.22, p90=39.31, p95=47.12, max=53.37,
    # mean=15.50. This is an established sparse/low-risk video (Phase 9-11's
    # own repeated finding).
    # RISK_ELEVATED_THRESHOLD=40.0 sits just above the real observed p90
    # (39.31) — the top ~10% of individual frames on this calm video would
    # nominally cross it, though persistence+hysteresis (decisions #1/#2)
    # require RISK_STATE_PERSISTENCE_FRAMES consecutive frames before that
    # actually confirms a transition.
    RISK_ELEVATED_THRESHOLD: float = 40.0
    # RISK_CRITICAL_THRESHOLD=65.0 sits ABOVE the real observed max (53.37)
    # on this video — genuine CRITICAL escalation requires conditions worse
    # than anything actually seen on this calm, sparse footage, which is
    # the intended behavior (this video should never itself reach CRITICAL
    # under normal operation).
    RISK_CRITICAL_THRESHOLD: float = 65.0
    # RISK_INCIDENT_THRESHOLD=85.0 — diagnostic-only (decision #4, does NOT
    # drive a state transition this phase, see risk_state.py's
    # incident_threshold_crossed). No real "this was genuinely a crush
    # incident" ground truth exists yet to calibrate against; set well above
    # RISK_CRITICAL_THRESHOLD to represent a genuinely severe, sustained
    # level, not merely "somewhat worse than critical."
    RISK_INCIDENT_THRESHOLD: float = 85.0
    # Phase 13 (decision #1): shared FALL threshold margin — each rise
    # threshold's fall counterpart is (rise_threshold - this margin), rather
    # than 3 independently-tunable fall thresholds (a deliberate
    # simplification of §40's plural "hysteresis margins" language; logged
    # in DECISIONS.md). Informed by real observed frame-to-frame risk_score
    # volatility on the SAME people_clip.mp4 preview re-run: mean absolute
    # delta between consecutive frames was ~9.58 across 148 frame-pairs.
    # 10.0 sits just above that real observed noise floor, so ordinary
    # single-frame jitter alone cannot cross both a rise and its fall
    # threshold and cause flapping.
    RISK_STATE_FALL_HYSTERESIS_MARGIN: float = 10.0
    # Phase 13 (decision #2): a candidate rise/fall condition must hold for
    # this many CONSECUTIVE update() calls before a transition is confirmed.
    # UNVALIDATED ENGINEERING JUDGMENT (logged in DECISIONS.md): 30 frames
    # (~1s at people_clip.mp4's 30fps) — the same "~1s at 30fps" reasoning
    # already used for BOTTLENECK_WINDOW_FRAMES (Phase 10): long enough that
    # a single anomalous frame can never trigger escalation (§14's explicit
    # requirement), short enough that a genuine sustained escalation isn't
    # felt as sluggish.
    RISK_STATE_PERSISTENCE_FRAMES: int = 30
    # Phase 1 placeholder, genuinely activated for the first time in Phase
    # 13 (decision #6): once a RISK trigger fires, further RISK firing at
    # the SAME severity level is suppressed for this many seconds (a
    # genuinely HIGHER escalation during the cooldown window overrides it —
    # see trigger_engine.py). No real VLM exists yet to empirically tune
    # this against; 30s is a reasonable placeholder-turned-real default,
    # long enough to avoid redundant re-triggering on ordinary risk_score
    # noise for the same escalation event, short enough that a later
    # genuinely new escalation isn't seriously delayed.
    VLM_COOLDOWN: int = 30
    # Phase 1 placeholder, genuinely activated for the first time in Phase
    # 13 (decision #7): FALLBACK fires every this-many REAL seconds
    # (timestamp-based, not frame-count-based), completely independent of
    # risk state — a periodic baseline check so the future VLM/LLM path is
    # exercised occasionally even during calm periods. 60s is a reasonable
    # placeholder-turned-real default; not empirically tuned (no VLM cost
    # model exists yet to optimize against).
    FALLBACK_ANALYSIS_INTERVAL: int = 60
    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    model_config = SettingsConfigDict(env_file=_ENV_FILE, extra="ignore")

    @model_validator(mode="after")
    def _validate_risk_score_weights_sum_to_one(self) -> "Settings":
        total = (
            self.RISK_SCORE_WEIGHT_PRESSURE
            + self.RISK_SCORE_WEIGHT_CONGESTION
            + self.RISK_SCORE_WEIGHT_BOTTLENECK
            + self.RISK_SCORE_WEIGHT_REVERSE_FLOW
        )
        if abs(total - 1.0) > 1e-6:
            raise ValueError(
                "RISK_SCORE_WEIGHT_PRESSURE + RISK_SCORE_WEIGHT_CONGESTION + "
                "RISK_SCORE_WEIGHT_BOTTLENECK + RISK_SCORE_WEIGHT_REVERSE_FLOW "
                f"must sum to 1.0, got {total}"
            )
        return self

    @model_validator(mode="after")
    def _validate_risk_state_threshold_range(self) -> "Settings":
        # Phase 13 follow-up: ORDERING alone (the next validator below) does
        # NOT catch a threshold on the wrong SCALE — Phase 1's stale 0-1
        # placeholders (0.5 < 0.75 < 0.9) satisfy strict ordering perfectly
        # while being catastrophically wrong for risk_score's actual 0-100
        # scale (see DECISIONS.md's "stale .env" Implementation-Discovered
        # Constraint — this validator is the direct fix for that real,
        # already-observed bug, not a hypothetical improvement). risk_score
        # is clipped to [0, 100] by compute_risk_score (§12/Phase 11) — every
        # threshold compared against it must fall within that same bound.
        for field_name in (
            "RISK_ELEVATED_THRESHOLD",
            "RISK_CRITICAL_THRESHOLD",
            "RISK_INCIDENT_THRESHOLD",
        ):
            value = getattr(self, field_name)
            if not (0.0 <= value <= 100.0):
                raise ValueError(
                    f"{field_name} must fall within [0, 100] (risk_score's own "
                    f"documented range, §12/Phase 11), got {value}. If this "
                    "value came from a real .env file, it is likely a stale "
                    "Phase 1 placeholder on the old 0-1 scale — see "
                    "DECISIONS.md's 'stale .env' Implementation-Discovered "
                    "Constraint."
                )
        return self

    @model_validator(mode="after")
    def _validate_risk_state_threshold_order(self) -> "Settings":
        # Decision #4: RISK_INCIDENT_THRESHOLD doesn't drive a transition
        # this phase, but it must still be validated in order — a
        # diagnostic flag that could fire BELOW the CRITICAL threshold that
        # gates it would be nonsensical.
        if not (
            self.RISK_ELEVATED_THRESHOLD
            < self.RISK_CRITICAL_THRESHOLD
            < self.RISK_INCIDENT_THRESHOLD
        ):
            raise ValueError(
                "RISK_ELEVATED_THRESHOLD < RISK_CRITICAL_THRESHOLD < "
                "RISK_INCIDENT_THRESHOLD must hold, got "
                f"{self.RISK_ELEVATED_THRESHOLD}, {self.RISK_CRITICAL_THRESHOLD}, "
                f"{self.RISK_INCIDENT_THRESHOLD}"
            )
        return self


settings = Settings()
