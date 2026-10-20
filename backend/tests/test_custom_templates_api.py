import json
from types import SimpleNamespace

import pytest

from inbound_api.routers import assistants as assistants_router
from inbound_api.settings import get_settings

GENERATED = json.dumps(
    {
        "label": "Garage bookings",
        "description": "Books MOTs and services and logs breakdowns.",
        "sets_up": ["MOT and service booking intake", "Breakdown urgency"],
        "greeting": "Hello, you are speaking with the AI assistant for {business_name}.",
        "instructions": 'Say: "{greeting}". Ask for the registration. Hours: {office_hours}.',
        "policies": ["Never quote a final price over the phone."],
        "services": [
            {
                "label": "MOT booking",
                "fields": [{"label": "Vehicle registration", "type": "string", "critical": True}],
            }
        ],
    }
)


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
async def test_custom_template_options_lists_sectors_and_use_cases(tenant_pair) -> None:
    client = tenant_pair["first"]["client"]
    response = await client.get("/templates/custom-options")
    assert response.status_code == 200
    body = response.json()
    assert "Legal services" in body["sectors"]
    assert "Take bookings and reservations" in body["use_cases"]


@pytest.mark.asyncio
async def test_custom_template_draft_builds_config_and_creates_assistant(
    tenant_pair, monkeypatch
) -> None:
    client = tenant_pair["first"]["client"]
    settings = get_settings()
    monkeypatch.setattr(settings, "google_api_key", "configured-test-key")
    fake = _FakeClient(GENERATED)
    monkeypatch.setattr(assistants_router, "make_genai_client", lambda _key: fake)

    response = await client.post(
        "/templates/custom-draft",
        json={
            "sector": "Automotive and garages",
            "use_case": "Take bookings and reservations",
            "details": "We also do tyres.",
            "language": "de-DE",
        },
    )
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["label"] == "Garage bookings"
    assert body["sets_up"] == ["MOT and service booking intake", "Breakdown urgency"]
    assert body["warnings"] == []
    assert body["config"]["language"] == "de-DE"
    assert list(body["config"]["services"]) == ["mot_booking"]
    prompt = fake.calls[0]["contents"]
    assert "Sector: Automotive and garages" in prompt
    assert "We also do tyres." in prompt

    created = await client.post(
        "/assistants", json={"name": "Garage", "template": None, "config": body["config"]}
    )
    assert created.status_code == 201, created.text
    assert created.json()["template"] is None


@pytest.mark.asyncio
async def test_custom_template_draft_requires_api_key(tenant_pair, monkeypatch) -> None:
    client = tenant_pair["first"]["client"]
    monkeypatch.setattr(get_settings(), "google_api_key", None)
    response = await client.post(
        "/templates/custom-draft", json={"sector": "Other", "use_case": "Qualify new leads"}
    )
    assert response.status_code == 503
