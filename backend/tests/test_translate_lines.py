import json
from types import SimpleNamespace
from uuid import uuid4

import pytest

from inbound_api.routers import assistants as assistants_router
from inbound_api.settings import get_settings


class _FakeClient:
    def __init__(self, text: str) -> None:
        self.calls: list[dict] = []

        async def generate_content(**arguments):
            self.calls.append(arguments)
            return SimpleNamespace(text=text)

        async def aclose():
            return None

        self.aio = SimpleNamespace(
            models=SimpleNamespace(generate_content=generate_content), aclose=aclose
        )


@pytest.mark.asyncio
async def test_translate_lines_uses_unsaved_lines_and_skips_empty_fields(
    tenant_pair, monkeypatch
) -> None:
    client = tenant_pair["first"]["client"]
    templates = (await client.get("/templates")).json()
    blank = next(template for template in templates if template["name"] == "blank")
    created = await client.post(
        "/assistants",
        json={"name": "Restaurant", "template": "blank", "config": blank["config"]},
    )
    assert created.status_code == 201, created.text
    assistant_id = created.json()["id"]

    fake = _FakeClient(
        json.dumps(
            {
                "greeting": "Ciao {{business_name}}",
                "call_settings": {"max_call_message": "La chiamata è terminata."},
            }
        )
    )
    monkeypatch.setattr(get_settings(), "google_api_key", "configured-test-key")
    monkeypatch.setattr(assistants_router, "make_genai_client", lambda _key: fake)

    response = await client.post(
        f"/assistants/{assistant_id}/translate-lines",
        json={
            "locale": "it-IT",
            "lines": {
                "greeting": "Hello {{business_name}}",
                "call_settings": {
                    "max_call_message": "The call has ended.",
                    "still_there_prompt": "   ",
                    "silence_goodbye_message": "",
                },
                "transfer_handoff_message": "",
                "transfer_failed_message": " ",
            },
        },
    )

    assert response.status_code == 200, response.text
    assert response.json() == {
        "greeting": "Ciao {{business_name}}",
        "call_settings": {"max_call_message": "La chiamata è terminata."},
    }
    call = fake.calls[0]
    assert call["config"]["response_mime_type"] == "application/json"
    assert call["config"]["response_schema"]["required"] == ["greeting", "call_settings"]
    assert "{{business_name}}" in call["contents"]
    assert "still_there_prompt" not in call["contents"]
    assert "transfer_handoff_message" not in call["contents"]

    unchanged = await client.get(f"/assistants/{assistant_id}")
    assert unchanged.status_code == 200
    assert unchanged.json()["config"]["greeting"] == blank["config"]["greeting"]


@pytest.mark.asyncio
async def test_translate_lines_rejects_unknown_assistant(tenant_pair) -> None:
    client = tenant_pair["first"]["client"]
    response = await client.post(
        f"/assistants/{uuid4()}/translate-lines",
        json={"locale": "it-IT", "lines": {"greeting": "Hello"}},
    )
    assert response.status_code == 404
