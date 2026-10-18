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
    )

    assert settings.voice_service_token == "configured-service-token"


def test_production_accepts_whatsapp_off_without_credentials() -> None:
    settings = Settings(
        app_env="production",
        voice_service_token="configured-service-token",
        whatsapp_mode="off",
    )

    assert settings.whatsapp_mode == "off"


def test_production_rejects_whatsapp_fake_mode() -> None:
    with pytest.raises(ValidationError, match="WHATSAPP_MODE=off"):
        Settings(
            app_env="production",
            voice_service_token="configured-service-token",
            whatsapp_mode="fake",
        )


@pytest.mark.parametrize(
    "missing_credential",
    ["whatsapp_app_secret", "whatsapp_verify_token", "whatsapp_access_token"],
)
def test_production_rejects_incomplete_whatsapp_graph_credentials(
    missing_credential: str,
) -> None:
    credentials = {
        "whatsapp_app_secret": "configured-app-secret",
        "whatsapp_verify_token": "configured-verify-token",
        "whatsapp_access_token": "configured-access-token",
    }
    credentials[missing_credential] = ""

    with pytest.raises(ValidationError):
        Settings(
            app_env="production",
            voice_service_token="configured-service-token",
            whatsapp_mode="graph",
            **credentials,
        )


def test_development_allows_default_voice_service_token() -> None:
    settings = Settings(
        app_env="development",
        voice_service_token=DEFAULT_VOICE_SERVICE_TOKEN,
    )

    assert settings.voice_service_token == DEFAULT_VOICE_SERVICE_TOKEN


@pytest.mark.asyncio
async def test_healthz_checks_database_with_application_role(api_client) -> None:
    response = await api_client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"ok": True}
