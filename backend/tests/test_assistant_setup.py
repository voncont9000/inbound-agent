import json
from types import SimpleNamespace
from uuid import UUID

import psycopg
import pytest
from sqlalchemy import func, select, text

from inbound_api.db import tenant_session
from inbound_api.jobs import run_next_job
from inbound_api.models import Assistant, Job, KnowledgeDigest, KnowledgeSource
from inbound_api.routers import assistant_setup as assistant_setup_router
from inbound_api.services import assistant_setup
from inbound_api.services import job_handlers as _job_handlers  # noqa: F401
from inbound_api.settings import get_settings


class _FakeModels:
    def __init__(self, text: str):
        self.text = text
        self.calls = []

    async def generate_content(self, **arguments):
        self.calls.append(arguments)
        return SimpleNamespace(
            text=self.text,
            usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=40),
        )


class _FakeClient:
    def __init__(self, text: str):
        self.aio = SimpleNamespace(models=_FakeModels(text), aclose=self._aclose)
        self.closed = False

    async def _aclose(self):
        self.closed = True


def _draft_json(**overrides) -> str:
    value = {
        "company_info": "A neighbourhood bakery making fresh bread every morning.",
        "faqs": [{"question": "When do you open?", "answer": "We open at 07:00."}],
        "office_hours": {
            "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
            "opens": "07:00",
            "closes": "16:00",
            "timezone": "Europe/London",
        },
        "price_list": [{"service": "Sourdough loaf", "price": "£4.50", "notes": ""}],
        "services": [{"label": "Bread orders", "template_key": None}],
    }
    value.update(overrides)
    return json.dumps(value)


def _record(text: str = "We bake bread locally.") -> dict:
    return {
        "source": "https://bakery.example",
        "title": "Bakery",
        "text": text,
        "fetched_at": "2026-05-10T10:00:00+00:00",
        "status": "ok" if text else "error",
        "chars": len(text),
        "errors": [],
    }


def _configure_draft(monkeypatch, *, response: str | None = None, records=None) -> _FakeClient:
    settings = get_settings()
    monkeypatch.setattr(settings, "google_api_key", "configured-test-key")
    monkeypatch.setattr(settings, "knowledge_allow_private_urls", True)
    client = _FakeClient(response if response is not None else _draft_json())
    monkeypatch.setattr(assistant_setup, "make_genai_client", lambda _key: client)
    monkeypatch.setattr(
        assistant_setup,
        "crawl_websites",
        lambda urls, **_kwargs: records if records is not None else [_record()],
    )
    return client


async def _create_draft_job(client, *, url="https://bakery.example"):
    templates = (await client.get("/templates")).json()
    template_name = templates[0]["name"]
    response = await client.post(
        "/assistant-setup/drafts",
        json={
            "business_name": "Northside Bakery",
            "website_url": url,
            "template": template_name,
        },
    )
    return response, template_name


async def test_setup_draft_job_succeeds_without_creating_assistant(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    fake_client = _configure_draft(monkeypatch)
    async with tenant_session(UUID(account["tenant_id"])) as session:
        before = await session.scalar(select(func.count()).select_from(Assistant))

    response, _ = await _create_draft_job(client)
    assert response.status_code == 202, response.text
    job_id = response.json()["id"]
    assert await run_next_job()
    result = (await client.get(f"/jobs/{job_id}")).json()

    assert result["status"] == "succeeded"
    assert result["result"]["draft"]["company_info"].startswith("A neighbourhood bakery")
    assert result["result"]["draft"]["faqs"][0]["question"] == "When do you open?"
    assert result["result"]["draft"]["price_list"][0]["price"] == "£4.50"
    assert result["result"]["warnings"] == []
    assert result["result"]["pages"][0]["title"] == "Bakery"
    assert "text" not in result["result"]["pages"][0]
    assert fake_client.closed
    assert len(fake_client.aio.models.calls) == 1
    assert fake_client.aio.models.calls[0]["config"] == {"response_mime_type": "application/json"}
    async with tenant_session(UUID(account["tenant_id"])) as session:
        after = await session.scalar(select(func.count()).select_from(Assistant))
    assert before == after


async def test_setup_draft_normalizes_bare_domain_in_job_payload(tenant_pair, monkeypatch) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    _configure_draft(monkeypatch)

    response, _ = await _create_draft_job(client, url="example.com")

    assert response.status_code == 202, response.text
    async with tenant_session(UUID(account["tenant_id"])) as session:
        job = await session.get(Job, UUID(response.json()["id"]))
    assert job is not None
    assert job.payload["website_url"] == "https://example.com"


@pytest.mark.parametrize(
    ("response_text", "expected_warning", "expected_company_info"),
    [
        (
            _draft_json(faqs=[{"question": "Valid?", "answer": "Yes."}, {"question": "Bad"}]),
            "Dropped 1 invalid FAQ.",
            "A neighbourhood bakery making fresh bread every morning.",
        ),
        ("not-json", "valid JSON", None),
    ],
)
async def test_setup_draft_job_keeps_partial_or_empty_draft_with_warnings(
    tenant_pair, monkeypatch, response_text, expected_warning, expected_company_info
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    _configure_draft(monkeypatch, response=response_text)
    response, _ = await _create_draft_job(client)
    assert response.status_code == 202, response.text

    assert await run_next_job()
    result = (await client.get(f"/jobs/{response.json()['id']}")).json()

    assert result["status"] == "succeeded"
    assert result["result"]["draft"]["company_info"] == expected_company_info
    assert any(expected_warning in warning for warning in result["result"]["warnings"])


async def test_setup_draft_job_fails_when_crawl_has_no_readable_text(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    _configure_draft(monkeypatch, records=[_record("")])
    response, _ = await _create_draft_job(client)
    assert response.status_code == 202

    assert await run_next_job()
    result = (await client.get(f"/jobs/{response.json()['id']}")).json()

    assert result["status"] == "failed"
    assert "The website returned no readable text." in result["error"]


async def test_setup_draft_rejects_private_url_and_missing_key(tenant_pair, monkeypatch) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_allow_private_urls", False)
    monkeypatch.setattr(settings, "google_api_key", "configured-test-key")
    monkeypatch.setattr(assistant_setup_router, "is_public_http_url", lambda _url: False)
    private, _ = await _create_draft_job(client, url="http://127.0.0.1/private")
    assert private.status_code == 422

    monkeypatch.setattr(settings, "knowledge_allow_private_urls", True)
    monkeypatch.setattr(settings, "google_api_key", None)
    no_key, _ = await _create_draft_job(client)
    assert no_key.status_code == 503
    assert no_key.json()["detail"] == "Website drafting is not configured."


async def test_setup_draft_owner_only_and_template_validation(
    tenant_pair, owner_database_url, monkeypatch
):
    account = tenant_pair["second"]
    with psycopg.connect(owner_database_url) as connection:
        connection.execute(
            "UPDATE memberships SET role = 'staff' WHERE tenant_id = %s AND user_id = %s",
            (account["tenant_id"], account["user_id"]),
        )
    client = account["client"]
    staff_response = await client.post(
        "/assistant-setup/drafts",
        json={
            "business_name": "Business",
            "website_url": "https://example.com",
            "template": "uk_law_firm",
        },
    )
    assert staff_response.status_code == 403

    owner = tenant_pair["first"]["client"]
    monkeypatch.setattr(get_settings(), "knowledge_allow_private_urls", True)
    invalid = await owner.post(
        "/assistant-setup/drafts",
        json={
            "business_name": "Business",
            "website_url": "https://example.com",
            "template": "unknown",
        },
    )
    assert invalid.status_code == 422


async def test_complete_setup_persists_only_accepted_data_and_optional_knowledge(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_allow_private_urls", True)
    templates = (await client.get("/templates")).json()
    template_name = templates[0]["name"]
    template_config = templates[0]["config"]
    accepted = {
        "company_info": "Accepted bakery summary.",
        "faqs": [{"question": "Accepted question?", "answer": "Accepted answer."}],
        "price_list": [{"service": "Bread", "price": "£5", "notes": ""}],
        "services": {
            "bread_orders": {
                "label": "Bread orders",
                "fields": [
                    {
                        "name": "caller_name",
                        "label": "Name",
                        "type": "string",
                        "required": False,
                        "critical": True,
                        "description": "",
                    }
                ],
            }
        },
    }
    response = await client.post(
        "/assistant-setup/complete",
        json={
            "name": "Northside",
            "business_name": "Northside Bakery",
            "template": template_name,
            "website_url": "bakery.example",
            "accepted": accepted,
        },
    )

    assert response.status_code == 201, response.text
    assistant = response.json()
    config = assistant["config"]
    assert config["business_name"] == "Northside Bakery"
    assert config["price_list"] == accepted["price_list"]
    assert config["office_hours"] == template_config["office_hours"]
    assert config["knowledge"]["faqs"] == accepted["faqs"]
    assert config["services"]["bread_orders"]["label"] == "Bread orders"
    assert config["services"]["bread_orders"]["fields"][0]["name"] == "caller_name"
    async with tenant_session(UUID(account["tenant_id"])) as session:
        digest = await session.scalar(
            select(KnowledgeDigest).where(KnowledgeDigest.assistant_id == UUID(assistant["id"]))
        )
        source = await session.scalar(
            select(KnowledgeSource).where(KnowledgeSource.assistant_id == UUID(assistant["id"]))
        )
        import_jobs = (
            (
                await session.execute(
                    text(
                        "SELECT payload FROM jobs "
                        "WHERE tenant_id = :tenant_id "
                        "AND kind = 'knowledge_import' "
                        "AND payload ->> 'assistant_id' = :assistant_id"
                    ),
                    {
                        "tenant_id": UUID(account["tenant_id"]),
                        "assistant_id": assistant["id"],
                    },
                )
            )
            .scalars()
            .all()
        )
    assert digest is not None
    assert digest.content == "Accepted bakery summary."
    assert digest.edited_by == UUID(account["user_id"])
    assert source is not None
    assert (source.kind, source.uri, source.status, source.manifest) == (
        "url",
        "https://bakery.example",
        "pending",
        {},
    )
    assert import_jobs == [{"assistant_id": assistant["id"]}]


async def test_complete_setup_with_template_defaults_creates_no_knowledge_rows(
    tenant_pair,
) -> None:
    client = tenant_pair["first"]["client"]
    template = (await client.get("/templates")).json()[0]
    response = await client.post(
        "/assistant-setup/complete",
        json={
            "name": "Default assistant",
            "business_name": "Business name",
            "template": template["name"],
            "accepted": {},
        },
    )

    assert response.status_code == 201, response.text
    assert response.json()["config"]["business_name"] == "Business name"
    assistant_id = UUID(response.json()["id"])
    async with tenant_session(UUID(tenant_pair["first"]["tenant_id"])) as session:
        digest_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDigest)
            .where(KnowledgeDigest.assistant_id == assistant_id)
        )
        source_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeSource)
            .where(KnowledgeSource.assistant_id == assistant_id)
        )
        import_jobs = (
            (
                await session.execute(
                    text(
                        "SELECT payload FROM jobs "
                        "WHERE tenant_id = :tenant_id "
                        "AND kind = 'knowledge_import' "
                        "AND payload ->> 'assistant_id' = :assistant_id"
                    ),
                    {
                        "tenant_id": UUID(tenant_pair["first"]["tenant_id"]),
                        "assistant_id": str(assistant_id),
                    },
                )
            )
            .scalars()
            .all()
        )
    assert digest_count == 0
    assert source_count == 0
    assert import_jobs == []


async def test_complete_setup_treats_blank_website_url_as_absent(tenant_pair) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    template = (await client.get("/templates")).json()[0]
    response = await client.post(
        "/assistant-setup/complete",
        json={
            "name": "Default assistant",
            "business_name": "Business name",
            "template": template["name"],
            "website_url": "   ",
            "accepted": {},
        },
    )

    assert response.status_code == 201, response.text
    assistant_id = UUID(response.json()["id"])
    async with tenant_session(UUID(account["tenant_id"])) as session:
        source_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeSource)
            .where(KnowledgeSource.assistant_id == assistant_id)
        )
        import_jobs = (
            (
                await session.execute(
                    text(
                        "SELECT payload FROM jobs "
                        "WHERE tenant_id = :tenant_id "
                        "AND kind = 'knowledge_import' "
                        "AND payload ->> 'assistant_id' = :assistant_id"
                    ),
                    {
                        "tenant_id": UUID(account["tenant_id"]),
                        "assistant_id": str(assistant_id),
                    },
                )
            )
            .scalars()
            .all()
        )
    assert source_count == 0
    assert import_jobs == []


async def test_complete_setup_rejects_invalid_website_url(tenant_pair) -> None:
    client = tenant_pair["first"]["client"]
    template = (await client.get("/templates")).json()[0]
    response = await client.post(
        "/assistant-setup/complete",
        json={
            "name": "Assistant",
            "business_name": "Business",
            "template": template["name"],
            "website_url": "file:///etc/passwd",
        },
    )

    assert response.status_code == 422
