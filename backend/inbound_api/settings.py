from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

BACKEND_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VOICE_SERVICE_TOKEN = "dev-only-voice-service-token"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(extra="ignore")

    app_env: str = "development"
    database_url: str = (
        "postgresql+psycopg://inbound_app_login:inbound_dev_only@localhost:5432/inbound"
    )
    migration_database_url: str = (
        "postgresql+psycopg://inbound_migrator:inbound_migrator@localhost:5432/inbound"
    )
    voice_service_token: str = DEFAULT_VOICE_SERVICE_TOKEN
    web_call_base_url: str | None = None
    whatsapp_app_secret: str | None = None
    whatsapp_verify_token: str | None = None
    whatsapp_access_token: str | None = None
    whatsapp_graph_base_url: str = "https://graph.facebook.com/v26.0"
    whatsapp_mode: Literal["fake", "graph", "off"] = "fake"
    text_agent_model: str = "gemini-3.5-flash-lite"
    extraction_model: str = "gemini-3.8-flash"
    message_extract_delay_seconds: int = 120
    fake_outbox_path: Path = BACKEND_ROOT / "var" / "whatsapp-outbox.jsonl"
    gmail_mode: Literal["off", "fake", "google"] = "fake"
    gmail_fake_dir: Path = BACKEND_ROOT / "var" / "gmail-fake"
    calendar_mode: Literal["off", "fake", "google"] = "fake"
    calendar_fake_path: Path = BACKEND_ROOT / "var" / "calendar-fake.json"
    calendar_fake_delay_ms: int = 0
    email_sender: Literal["off", "fake", "gmail", "resend"] = "fake"
    resend_api_key: str | None = None
    email_from: str | None = None
    resend_base_url: str = "https://api.resend.com"
    sheets_writer: Literal["off", "fake", "google"] = "fake"
    webhook_sender: Literal["off", "fake", "http"] = "fake"
    post_actions_fake_dir: Path = BACKEND_ROOT / "var" / "post-actions"
    post_action_webhook_allow_private_urls: bool = False
    app_base_url: str = "http://localhost:5173"
    google_oauth_client_id: str | None = None
    google_oauth_client_secret: str | None = None
    google_oauth_redirect_url: str | None = None
    gmail_pubsub_topic: str | None = None
    gmail_pubsub_audience: str | None = None
    gmail_pubsub_service_account: str | None = None
    gmail_push_token: str | None = None
    gmail_reconcile_interval_seconds: int = 300
    credentials_encryption_key: str | None = None
    price_table_path: Path = (
        Path(__file__).resolve().parents[2] / "voice" / "config" / "prices.yaml"
    )
    session_ttl_hours: int = 336
    session_cookie_secure: bool = True
    allowed_origins: list[str] = ["http://localhost:5173"]
    upload_dir: Path = BACKEND_ROOT / "var" / "uploads"
    max_upload_bytes: int = 20 * 1024 * 1024
    recording_max_bytes: int = 200 * 1024 * 1024
    templates_dir: Path = Path(__file__).resolve().parents[2] / "voice" / "config" / "templates"
    google_api_key: str | None = None
    knowledge_model: str = "gemini-3.8-flash"
    knowledge_max_pages: int = 50
    knowledge_request_delay: float = 0.5
    knowledge_allow_private_urls: bool = False
    setup_draft_model: str = "gemini-3.1-flash-lite"
    setup_draft_max_pages: int = 10
    job_max_attempts: int = 3
    job_retry_base_seconds: float = 10
    job_lease_seconds: int = 900
    worker_poll_seconds: float = 1.0

    @field_validator("upload_dir", mode="before")
    @classmethod
    def resolve_upload_dir(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path

    @field_validator("fake_outbox_path", mode="before")
    @classmethod
    def resolve_fake_outbox_path(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path

    @field_validator("gmail_fake_dir", mode="before")
    @classmethod
    def resolve_gmail_fake_dir(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path

    @field_validator("calendar_fake_path", mode="before")
    @classmethod
    def resolve_calendar_fake_path(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path

    @field_validator("post_actions_fake_dir", mode="before")
    @classmethod
    def resolve_post_actions_fake_dir(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path

    @field_validator("price_table_path", mode="before")
    @classmethod
    def resolve_price_table_path(cls, value: str | Path) -> Path:
        path = Path(value)
        return path if path.is_absolute() else BACKEND_ROOT.parent / path

    @model_validator(mode="after")
    def require_production_voice_service_token(self) -> "Settings":
        if self.app_env not in {"development", "test"} and (
            not self.voice_service_token.strip()
            or self.voice_service_token == DEFAULT_VOICE_SERVICE_TOKEN
        ):
            raise ValueError(
                "VOICE_SERVICE_TOKEN must be non-empty and non-default outside development and test"
            )
        if self.app_env not in {"development", "test"}:
            if self.whatsapp_mode == "fake":
                raise ValueError(
                    "WhatsApp production requires WHATSAPP_MODE=off or graph mode with "
                    "WHATSAPP_APP_SECRET, WHATSAPP_VERIFY_TOKEN, and WHATSAPP_ACCESS_TOKEN"
                )
            if self.whatsapp_mode == "graph" and (
                not (self.whatsapp_app_secret or "").strip()
                or not (self.whatsapp_verify_token or "").strip()
                or not (self.whatsapp_access_token or "").strip()
            ):
                raise ValueError(
                    "WhatsApp graph mode requires WHATSAPP_APP_SECRET, "
                    "WHATSAPP_VERIFY_TOKEN, and WHATSAPP_ACCESS_TOKEN; "
                    "use WHATSAPP_MODE=off to disable WhatsApp"
                )
        if self.app_env not in {"development", "test"} and self.gmail_mode == "fake":
            raise ValueError("Gmail production requires google mode or off mode")
        if self.app_env not in {"development", "test"} and self.calendar_mode == "fake":
            raise ValueError("Calendar production requires google mode or off mode")
        if self.calendar_mode == "google" and self.gmail_mode != "google":
            raise ValueError("Google Calendar mode requires GMAIL_MODE=google")
        if (
            self.app_env not in {"development", "test"}
            and self.gmail_mode == "google"
            and (
                not (self.google_oauth_client_id or "").strip()
                or not (self.google_oauth_client_secret or "").strip()
                or not (self.google_oauth_redirect_url or "").strip()
                or not (self.gmail_pubsub_topic or "").strip()
                or not (self.gmail_pubsub_audience or "").strip()
                or not (self.gmail_pubsub_service_account or "").strip()
                or not (self.credentials_encryption_key or "").strip()
            )
        ):
            raise ValueError(
                "Gmail production google mode requires Google OAuth/Pub/Sub settings "
                "and CREDENTIALS_ENCRYPTION_KEY"
            )
        if self.app_env not in {"development", "test"}:
            if (
                self.email_sender == "fake"
                or self.sheets_writer == "fake"
                or self.webhook_sender == "fake"
            ):
                raise ValueError(
                    "Fake post-action providers are only allowed in development and test"
                )
            if self.email_sender == "resend" and (
                not (self.resend_api_key or "").strip() or not (self.email_from or "").strip()
            ):
                raise ValueError("Resend requires RESEND_API_KEY and EMAIL_FROM")
            if (
                self.email_sender == "gmail" or self.sheets_writer == "google"
            ) and self.gmail_mode != "google":
                raise ValueError("Google post-action providers require GMAIL_MODE=google")
            if self.post_action_webhook_allow_private_urls:
                raise ValueError("Private webhook URLs are only allowed in development and test")
            if (
                self.webhook_sender == "http"
                and not (self.credentials_encryption_key or "").strip()
            ):
                raise ValueError("Webhook signing requires CREDENTIALS_ENCRYPTION_KEY")
        return self


@lru_cache
def get_settings() -> Settings:
    return Settings()
