from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

# Repo-root .env, resolved absolutely so settings load correctly regardless of
# the process's current working directory (uvicorn, alembic, pytest, and
# scripts/ are all invoked from different cwds).
_ENV_FILE = Path(__file__).resolve().parents[3] / ".env"


class Settings(BaseSettings):
    DATABASE_URL: str = "postgresql+psycopg2://user:password@localhost:5432/crowdshield"
    DATABASE_URL_TEST: str = "postgresql+psycopg2://user:password@localhost:5432/crowdshield_test"
    AUTH_SECRET: str = "changeme"
    JWT_SECRET_KEY: str = "changeme"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 30
    VIDEO_STORAGE_PATH: str = "storage/videos"
    LOG_LEVEL: str = "INFO"
    DETECTOR_MODEL: str = "yolov8n"
    DETECTOR_RUNTIME: str = "cpu"
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
