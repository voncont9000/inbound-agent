from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    database_url: str = (
        "postgresql+psycopg://inbound_app_login:inbound_dev_only@localhost:5432/inbound"
    )
    voice_service_token: str = "dev-only-voice-service-token"


@lru_cache
def get_settings() -> Settings:
    return Settings()
