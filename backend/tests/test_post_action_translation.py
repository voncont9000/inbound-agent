import json
from types import SimpleNamespace

import pytest

from inbound_api.post_actions import translation
from inbound_api.settings import Settings


class FakeModels:
    def __init__(self, text):
        self.text = text
        self.calls = []

    async def generate_content(self, **arguments):
        self.calls.append(arguments)
        if isinstance(self.text, Exception):
            raise self.text
        return SimpleNamespace(text=self.text)


class FakeClient:
    def __init__(self, text):
        self.models = FakeModels(text)
        self.closed = False

    async def aclose(self):
        self.closed = True


def _client(client):
    return SimpleNamespace(aio=SimpleNamespace(models=client.models, aclose=client.aclose))


@pytest.mark.asyncio
async def test_translate_email_uses_one_text_agent_call_and_returns_email_fields(monkeypatch):
    translated = {
        "subject": "Grazie per averci contattato",
        "body": "Grazie per averci contattato. Il nostro team ti risponderà presto.",
        "footer_label": "Inviato da",
    }
    client = FakeClient(json.dumps(translated))
    monkeypatch.setattr(translation, "make_genai_client", lambda api_key: _client(client))

    result = await translation.translate_email(
        subject="Thank you",
        body="Thank you for contacting us. Our team will reply shortly.",
        footer_label="Sent by",
        target_language="Italian",
        settings=Settings(google_api_key="test-key", text_agent_model="flash-lite-test"),
    )

    assert result is not None
    assert result.subject == translated["subject"]
    assert result.body == translated["body"]
    assert result.footer_label == translated["footer_label"]
    assert len(client.models.calls) == 1
    call = client.models.calls[0]
    assert call["model"] == "flash-lite-test"
    assert call["config"]["response_mime_type"] == "application/json"
    assert "Italian" in call["contents"]
    assert "phone numbers" in call["contents"]
    assert client.closed


@pytest.mark.asyncio
async def test_translate_email_returns_none_without_api_key(monkeypatch):
    monkeypatch.setattr(
        translation,
        "make_genai_client",
        lambda _api_key: pytest.fail("No client should be created without an API key"),
    )

    result = await translation.translate_email(
        subject="Thank you",
        body="Thank you for contacting us.",
        footer_label="Sent by",
        target_language="Italian",
        settings=Settings(google_api_key=None),
    )

    assert result is None


@pytest.mark.asyncio
async def test_translate_email_returns_none_for_invalid_response_or_api_error(monkeypatch):
    invalid_response = FakeClient(
        json.dumps(
            {
                "subject": "Grazie",
                "body": "Too short",
                "footer_label": "Inviato da",
            }
        )
    )
    monkeypatch.setattr(
        translation,
        "make_genai_client",
        lambda _api_key: _client(invalid_response),
    )
    invalid = await translation.translate_email(
        subject="Thank you",
        body="Thank you for contacting us. Our team will reply shortly.",
        footer_label="Sent by",
        target_language="Italian",
        settings=Settings(google_api_key="test-key"),
    )
    assert invalid is None

    failing_client = FakeClient(RuntimeError("provider failure"))
    monkeypatch.setattr(
        translation,
        "make_genai_client",
        lambda _api_key: _client(failing_client),
    )
    failed = await translation.translate_email(
        subject="Thank you",
        body="Thank you for contacting us. Our team will reply shortly.",
        footer_label="Sent by",
        target_language="Italian",
        settings=Settings(google_api_key="test-key"),
    )
    assert failed is None
    assert failing_client.closed
