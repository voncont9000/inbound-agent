import pytest
from pydantic import ValidationError

from inbound_api.settings import DEFAULT_VOICE_SERVICE_TOKEN, Settings


@pytest.mark.parametrize("token", ["", DEFAULT_VOICE_SERVICE_TOKEN])
def test_production_rejects_empty_or_default_voice_service_token(token: str) -> None:
    with pytest.raises(ValidationError):
        Settings(app_env="production", voice_service_token=token)


def test_production_accepts_configured_voice_service_token() -> None:
    settings = Settings(
        app_env="production",
        voice_service_token="configured-service-token",
        whatsapp_mode="graph",
        whatsapp_app_secret="configured-app-secret",
        whatsapp_verify_token="configured-verify-token",
        whatsapp_access_token="configured-access-token",
        gmail_mode="off",
        calendar_mode="off",
    )

    assert settings.voice_service_token == "configured-service-token"


def test_development_allows_default_voice_service_token() -> None:
    settings = Settings(
        app_env="development",
        voice_service_token=DEFAULT_VOICE_SERVICE_TOKEN,
    )

    assert settings.voice_service_token == DEFAULT_VOICE_SERVICE_TOKEN
