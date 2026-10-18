from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = (
        "postgresql+psycopg://inbound_app_login:inbound_dev_only@localhost:5432/inbound"
    )
    migration_database_url: str = (
        "postgresql+psycopg://inbound_migrator:inbound_migrator@localhost:5432/inbound"
    )
    voice_service_token: str = "dev-only-voice-service-token"
    session_ttl_hours: int = 336
    session_cookie_secure: bool = False
    upload_dir: Path = BACKEND_ROOT / "var" / "uploads"
    max_upload_bytes: int = 20 * 1024 * 1024
    templates_dir: Path = Path(__file__).resolve().parents[2] / "voice" / "config" / "templates"
    google_api_key: str | None = None
    knowledge_model: str = "gemini-3.8-flash"
    knowledge_max_pages: int = 50
    knowledge_request_delay: float = 0.5
    job_max_attempts: int = 3
    job_retry_base_seconds: float = 10
    job_lease_seconds: int = 900
    worker_poll_seconds: float = 1.0

    @field_validator("upload_dir", mode="before")
    @classmethod
    def resolve_upload_dir(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path


@lru_cache
def get_settings() -> Settings:
    return Settings()
