import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from types import SimpleNamespace
from uuid import UUID, uuid4

import psycopg
from sqlalchemy import func, select

from inbound_api.db import tenant_session
from inbound_api.jobs import current_job, enqueue, run_next_job
from inbound_api.models import Job, KnowledgeChunk, KnowledgeDigest, KnowledgeSource
from inbound_api.services import files_cleanup, knowledge_import
from inbound_api.services import job_handlers as _job_handlers  # noqa: F401
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


async def _drain_claimable_jobs() -> None:
    for _ in range(100):
        if not await run_next_job():
            return
    raise AssertionError("Claimable jobs did not drain")


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


async def test_private_url_source_is_rejected_by_default(tenant_pair) -> None:
    client = tenant_pair["first"]["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "SSRF assistant", "template": "uk_law_firm", "config": config},
    )
    response = await client.post(
        f"/assistants/{assistant.json()['id']}/knowledge/sources",
        json={"kind": "url", "uri": "http://127.0.0.1/private"},
    )
    assert response.status_code == 422


async def test_bare_domain_url_source_is_normalized_before_storage(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    monkeypatch.setattr(get_settings(), "knowledge_allow_private_urls", True)
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "Website assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]

    response = await client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        json={"kind": "url", "uri": "  example.com/about  "},
    )

    assert response.status_code == 201, response.text
    assert response.json()["uri"] == "https://example.com/about"
    async with tenant_session(UUID(account["tenant_id"])) as session:
        source = await session.get(KnowledgeSource, UUID(response.json()["id"]))
    assert source is not None
    assert source.uri == "https://example.com/about"


async def test_failed_refresh_retains_chunks_and_does_not_replace_digest(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "Refresh assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_allow_private_urls", True)
    monkeypatch.setattr(settings, "google_api_key", "test-key")
    fake_client = _FakeClient()
    monkeypatch.setattr(knowledge_import, "make_genai_client", lambda _key: fake_client)
    source_urls = {
        "https://example.com/first",
        "https://example.com/second",
    }
    mode = {"value": "initial"}

    def fake_crawl(urls, **_kwargs):
        records = []
        for url in urls:
            if mode["value"] == "initial":
                text = f"Original knowledge for {url}."
                status = "ok"
                errors = []
            elif mode["value"] == "partial" and url.endswith("/first"):
                text = ""
                status = "error"
                errors = ["Fixture fetch failed."]
            elif mode["value"] == "partial":
                text = "Fresh second-source knowledge."
                status = "ok"
                errors = []
            else:
                text = ""
                status = "error"
                errors = ["Fixture refresh failed."]
            records.append(
                {
                    "source": url,
                    "title": url.rsplit("/", 1)[-1],
                    "text": text,
                    "fetched_at": "2026-03-01T12:00:00+00:00",
                    "status": status,
                    "chars": len(text),
                    "errors": errors,
                }
            )
        return records

    monkeypatch.setattr(knowledge_import, "crawl_websites", fake_crawl)
    source_ids = {}
    for url in sorted(source_urls):
        response = await client.post(
            f"/assistants/{assistant_id}/knowledge/sources",
            json={"kind": "url", "uri": url},
        )
        assert response.status_code == 201, response.text
        source_ids[url] = UUID(response.json()["id"])

    async def import_once() -> str:
        response = await client.post(f"/assistants/{assistant_id}/knowledge/import")
        assert response.status_code == 202, response.text
        job_id = response.json()["id"]
        assert await run_next_job()
        return (await client.get(f"/jobs/{job_id}")).json()["status"]

    assert await import_once() == "succeeded"
    async with tenant_session(UUID(account["tenant_id"])) as session:
        original_chunks = {
            source_id: [
                (chunk.title, chunk.text, chunk.sha256)
                for chunk in await session.scalars(
                    select(KnowledgeChunk)
                    .where(KnowledgeChunk.source_id == source_id)
                    .order_by(KnowledgeChunk.chunk_index)
                )
            ]
            for source_id in source_ids.values()
        }
        source_rows = list(
            await session.scalars(
                select(KnowledgeSource).where(KnowledgeSource.id.in_(list(source_ids.values())))
            )
        )
        fetched_at = {source.uri: source.fetched_at for source in source_rows}

    mode["value"] = "partial"
    assert await import_once() == "succeeded"
    sources_response = await client.get(f"/assistants/{assistant_id}/knowledge/sources")
    sources_by_uri = {source["uri"]: source for source in sources_response.json()["items"]}
    failed_source = sources_by_uri["https://example.com/first"]
    assert failed_source["status"] == "failed"
    assert failed_source["manifest"]["status"] == "error"
    assert "Fixture fetch failed" in failed_source["manifest"]["last_error"]
    assert (
        datetime.fromisoformat(failed_source["fetched_at"].replace("Z", "+00:00"))
        == fetched_at["https://example.com/first"]
    )
    async with tenant_session(UUID(account["tenant_id"])) as session:
        retained = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.source_id == source_ids["https://example.com/first"])
                .order_by(KnowledgeChunk.chunk_index)
            )
        )
    assert [(chunk.title, chunk.text, chunk.sha256) for chunk in retained] == original_chunks[
        source_ids["https://example.com/first"]
    ]
    prompt = fake_client.aio.models.arguments["contents"]
    assert "Original knowledge for https://example.com/first." in prompt
    assert "Fresh second-source knowledge." in prompt
    previous_digest = await client.get(f"/assistants/{assistant_id}/knowledge/digest")
    previous_digest_id = previous_digest.json()["id"]

    mode["value"] = "failed"
    assert await import_once() == "failed"
    latest_digest = await client.get(f"/assistants/{assistant_id}/knowledge/digest")
    assert latest_digest.json()["id"] == previous_digest_id
    all_sources = await client.get(f"/assistants/{assistant_id}/knowledge/sources")
    assert all(source["manifest"]["status"] == "error" for source in all_sources.json()["items"])
    async with tenant_session(UUID(account["tenant_id"])) as session:
        final_chunks = list(
            await session.scalars(
                select(KnowledgeChunk)
                .where(KnowledgeChunk.source_id.in_(list(source_ids.values())))
                .order_by(KnowledgeChunk.source_id, KnowledgeChunk.chunk_index)
            )
        )
        digest_count = await session.scalar(
            select(func.count())
            .select_from(KnowledgeDigest)
            .where(KnowledgeDigest.assistant_id == UUID(assistant_id))
        )
    assert digest_count == 2
    assert len(final_chunks) == sum(len(chunks) for chunks in original_chunks.values())


async def test_failed_refresh_does_not_write_manifest_after_lease_loss(
    tenant_pair, monkeypatch, owner_database_url
) -> None:
    await _drain_claimable_jobs()
    account = tenant_pair["first"]
    client = account["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "Stale import assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]
    settings = get_settings()
    monkeypatch.setattr(settings, "knowledge_allow_private_urls", True)
    monkeypatch.setattr(settings, "google_api_key", "test-key")
    monkeypatch.setattr(knowledge_import, "make_genai_client", lambda _key: _FakeClient())
    source_response = await client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        json={"kind": "url", "uri": "https://example.com/stale-lease"},
    )
    assert source_response.status_code == 201, source_response.text
    source_before = source_response.json()

    def lose_lease(urls, **_kwargs):
        job = current_job.get()
        assert job is not None
        with psycopg.connect(owner_database_url) as connection:
            connection.execute(
                "UPDATE jobs SET lease_token = %s WHERE id = %s",
                (uuid4(), job[0]),
            )
        return [
            {
                "source": url,
                "title": "stale-lease",
                "text": "",
                "fetched_at": datetime.now(UTC).isoformat(),
                "status": "error",
                "chars": 0,
                "errors": ["Fixture fetch failed."],
            }
            for url in urls
        ]

    monkeypatch.setattr(knowledge_import, "crawl_websites", lose_lease)
    import_response = await client.post(f"/assistants/{assistant_id}/knowledge/import")
    assert import_response.status_code == 202, import_response.text
    assert await run_next_job()
    source_after = (await client.get(f"/assistants/{assistant_id}/knowledge/sources")).json()[
        "items"
    ][0]
    assert source_after["status"] == source_before["status"]
    assert source_after["manifest"] == source_before["manifest"]
    assert source_after["fetched_at"] == source_before["fetched_at"]


async def test_assistant_delete_cleans_uploaded_files(tenant_pair, monkeypatch, tmp_path) -> None:
    client = tenant_pair["first"]["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "Cleanup assistant", "template": "uk_law_firm", "config": config},
    )
    assistant_id = assistant.json()["id"]
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    upload = await client.post(
        f"/assistants/{assistant_id}/knowledge/sources",
        files={"file": ("cleanup.txt", b"Remove this file.", "text/plain")},
    )
    assert upload.status_code == 201, upload.text
    directory = tmp_path / tenant_pair["first"]["tenant_id"] / assistant_id
    assert directory.is_dir()
    assert (await client.delete(f"/assistants/{assistant_id}")).status_code == 204
    assert await run_next_job()
    assert not directory.exists()


async def test_cleanup_job_succeeds_when_directory_is_missing(
    tenant_pair, monkeypatch, tmp_path
) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    assistant_id = uuid4()
    monkeypatch.setattr(get_settings(), "upload_dir", tmp_path)
    async with tenant_session(tenant_id) as session:
        job_id = await enqueue(
            session, "knowledge_files_cleanup", {"assistant_id": str(assistant_id)}
        )
    assert await run_next_job()
    async with tenant_session(tenant_id) as session:
        job = await session.get(Job, job_id)
    assert job is not None
    assert job.status.value == "succeeded"


async def test_cleanup_permission_error_retries_job(tenant_pair, monkeypatch, tmp_path) -> None:
    await _drain_claimable_jobs()
    tenant_id = UUID(tenant_pair["first"]["tenant_id"])
    assistant_id = uuid4()
    settings = get_settings()
    monkeypatch.setattr(settings, "upload_dir", tmp_path)
    monkeypatch.setattr(settings, "job_retry_base_seconds", 3600)

    def deny_removal(*_args, **_kwargs):
        raise PermissionError("cleanup denied")

    monkeypatch.setattr(files_cleanup.shutil, "rmtree", deny_removal)
    async with tenant_session(tenant_id) as session:
        job_id = await enqueue(
            session, "knowledge_files_cleanup", {"assistant_id": str(assistant_id)}
        )
    assert await run_next_job()
    async with tenant_session(tenant_id) as session:
        job = await session.get(Job, job_id)
    assert job is not None
    assert job.status.value == "queued"
    assert job.attempts == 1
    assert job.error == "PermissionError: cleanup denied"


async def test_oversized_content_length_rejected_before_form_parse(
    tenant_pair, monkeypatch
) -> None:
    client = tenant_pair["first"]["client"]
    templates = (await client.get("/templates")).json()
    config = next(item["config"] for item in templates if item["name"] == "uk_law_firm")
    assistant = await client.post(
        "/assistants",
        json={"name": "Upload limit assistant", "template": "uk_law_firm", "config": config},
    )
    settings = get_settings()
    monkeypatch.setattr(settings, "max_upload_bytes", 1024)
    declared_length = settings.max_upload_bytes + 64 * 1024 + 1
    request = client.build_request(
        "POST",
        f"/assistants/{assistant.json()['id']}/knowledge/sources",
        content=b"--boundary--",
        headers={
            "Content-Type": "multipart/form-data; boundary=boundary",
            "Content-Length": str(declared_length),
        },
    )
    response = await client.send(request)
    assert response.status_code == 413


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
    monkeypatch.setattr(settings, "knowledge_allow_private_urls", True)
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
    monkeypatch.setattr(get_settings(), "knowledge_allow_private_urls", True)
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
