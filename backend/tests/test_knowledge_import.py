import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID

from sqlalchemy import func, select

from inbound_api.db import tenant_session
from inbound_api.jobs import run_next_job
from inbound_api.models import KnowledgeChunk, KnowledgeSource
from inbound_api.services import job_handlers as _job_handlers  # noqa: F401
from inbound_api.services import knowledge_import
from inbound_api.settings import get_settings


class _FakeModels:
    async def generate_content(self, **arguments):
        self.arguments = arguments
        return SimpleNamespace(
            text="## Fees\nThe fixed fee is £595 plus VAT [file:fees.md].",
            usage_metadata=SimpleNamespace(prompt_token_count=20, candidates_token_count=8),
        )


class _FakeClient:
    def __init__(self):
        self.aio = SimpleNamespace(models=_FakeModels(), aclose=self._aclose)

    async def _aclose(self):
        return None


async def test_file_import_writes_chunks_digest_and_replaces_chunks(
    tenant_pair, monkeypatch, tmp_path
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    created = await client.post(
        "/assistants",
        json={"name": "Knowledge assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = created.json()["id"]
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "google_api_key", "test-key")
    fake_client = _FakeClient()
    monkeypatch.setattr(knowledge_import, "make_genai_client", lambda _key: fake_client)

    source = await client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        files={"file": ("fees.md", b"The fixed fee is \xc2\xa3595 plus VAT.", "text/markdown")},
    )
    assert source.status_code == 201, source.text
    bad_upload = await client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        files={"file": ("payload.exe", b"not allowed", "application/octet-stream")},
    )
    assert bad_upload.status_code == 422
    job = await client.post(f"/assistants/{assistant_id}/knowledge/import")
    assert job.status_code == 202, job.text
    job_id = job.json()["id"]
    assert await run_next_job()

    job_result = await client.get(f"/jobs/{job_id}")
    assert job_result.json()["status"] == "succeeded"
    source_list = await client.get(f"/assistants/{assistant_id}/knowledge/sources")
    source_data = source_list.json()["items"][0]
    assert source_data["status"] == "ready"
    assert source_data["manifest"]["chunk_count"] == 1
    async with tenant_session(UUID(account["tenant_id"])) as session:
        chunk_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == UUID(source_data["id"]))
        )
        stored_source = await session.get(KnowledgeSource, UUID(source_data["id"]))
        assert stored_source is not None
        assert not stored_source.manifest["stored_path"].startswith("/")
    assert chunk_count == 1
    digest = await client.get(f"/assistants/{assistant_id}/knowledge/digest")
    assert digest.status_code == 200
    assert "£595" in digest.json()["content"]

    second_import = await client.post(f"/assistants/{assistant_id}/knowledge/import")
    assert second_import.status_code == 202
    assert await run_next_job()
    async with tenant_session(UUID(account["tenant_id"])) as session:
        chunk_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeChunk)
            .where(KnowledgeChunk.source_id == UUID(source_data["id"]))
        )
    assert chunk_count == 1


async def test_url_import_obeys_robots_and_persists_page_manifest(tenant_pair, monkeypatch) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "URL knowledge assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_request_delay", 0)
    monkeypatch.setattr(settings, "google_api_key", "test-key")
    fake_client = _FakeClient()
    monkeypatch.setattr(knowledge_import, "make_genai_client", lambda _key: fake_client)
    requested_paths = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested_paths.append(self.path)
            pages = {
                "/robots.txt": (
                    "text/plain",
                    "User-agent: *\nDisallow: /blocked\n",
                ),
                "/": (
                    "text/html; charset=utf-8",
                    "<html><head><title>Home</title></head><body><main>"
                    "<p>The firm offers residential conveyancing services.</p>"
                    '<a href="/page-one">Page one</a><a href="/page-two">Page two</a>'
                    '<a href="/blocked">Blocked page</a></main></body></html>',
                ),
                "/page-one": (
                    "text/html; charset=utf-8",
                    "<html><head><title>Page One</title></head><body><main>"
                    "<p>Page one explains the first step in the process.</p>"
                    "</main></body></html>",
                ),
                "/page-two": (
                    "text/html; charset=utf-8",
                    "<html><head><title>Page Two</title></head><body><main>"
                    "<p>Page two explains the second step in the process.</p>"
                    "</main></body></html>",
                ),
                "/blocked": (
                    "text/html; charset=utf-8",
                    "<html><body><main>"
                    "<p>This page must not be requested.</p>"
                    "</main></body></html>",
                ),
            }
            content_type, content = pages.get(
                self.path,
                ("text/plain", "not found"),
            )
            body = content.encode()
            self.send_response(200 if self.path in pages else 404)
            self.send_header("Content-Type", content_type)
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        source_response = await client.post(
            f"/assistants/{assistant_id}/knowledge/sources",
            json={"kind": "url", "uri": f"http://127.0.0.1:{server.server_port}/"},
        )
        assert source_response.status_code == 201, source_response.text
        source_id = source_response.json()["id"]
        job = await client.post(f"/assistants/{assistant_id}/knowledge/import")
        assert job.status_code == 202, job.text
        assert await run_next_job()
        job_result = await client.get(f"/jobs/{job.json()['id']}")
        assert job_result.json()["status"] == "succeeded"

        sources = await client.get(f"/assistants/{assistant_id}/knowledge/sources")
        imported_source = next(item for item in sources.json()["items"] if item["id"] == source_id)
        assert imported_source["status"] == "ready"
        manifest = imported_source["manifest"]
        assert manifest["chunk_count"] > 0
        assert {page["title"] for page in manifest["pages"] if page["status"] == "ok"} == {
            "Home",
            "Page One",
            "Page Two",
        }
        assert any(
            page["status"] == "robots_disallowed" and "/blocked" in page["source"]
            for page in manifest["pages"]
        )
        assert "/blocked" not in requested_paths
        assert {"/", "/page-one", "/page-two", "/robots.txt"} <= set(requested_paths)

        async with tenant_session(UUID(account["tenant_id"])) as session:
            chunk_count = await session.scalar(
                select(func.count())
                .select_from(KnowledgeChunk)
                .where(KnowledgeChunk.source_id == UUID(source_id))
            )
        assert chunk_count == manifest["chunk_count"]
    finally:
        server.shutdown()
        server.server_close()
        thread.join()


async def test_import_without_google_key_fails_before_crawling(tenant_pair, monkeypatch) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "No key assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]
    source = await client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        json={"kind": "url", "uri": "http://127.0.0.1:1/should-not-fetch"},
    )
    assert source.status_code == 201
    monkeypatch.setattr(get_settings(), "google_api_key", "")
    monkeypatch.setattr(
        knowledge_import,
        "crawl_websites",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(AssertionError("crawl must not run")),
    )
    job = await client.post(f"/assistants/{assistant_id}/knowledge/import")
    assert job.status_code == 202
    assert await run_next_job()
    result = await client.get(f"/jobs/{job.json()['id']}")
    assert result.json()["status"] == "failed"
    assert "GOOGLE_API_KEY is not configured" in result.json()["error"]
