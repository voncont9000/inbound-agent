import asyncio
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from inbound_core.knowledge import (
    build_chunks,
    build_manifest,
    chunk_text,
    crawl_websites,
    generate_digest,
    read_files,
    truncate_digest_at_section_boundary,
)


class _FakeModels:
    async def generate_content(self, **arguments):
        self.arguments = arguments
        return SimpleNamespace(
            text="## Services\nSource-backed details.",
            usage_metadata=SimpleNamespace(prompt_token_count=12, candidates_token_count=4),
        )


class _FakeClient:
    def __init__(self):
        self.aio = SimpleNamespace(models=_FakeModels())


def test_file_reader_override_and_chunk_manifest(tmp_path: Path) -> None:
    path = tmp_path / "source.md"
    path.write_text("Fee details " * 140, encoding="utf-8")

    records = read_files([path], display_names=["client-upload.md"])
    chunks = build_chunks(records)
    manifest = build_manifest(
        records,
        business_name="Example",
        model="test-model",
        max_digest_chars=500,
        usage={"input_tokens": 1, "output_tokens": 2},
    )

    assert records[0]["source"] == "file:client-upload.md"
    assert len(chunks) > 1
    assert all(len(chunk["text"]) <= 1_200 for chunk in chunks)
    assert len(chunks[0]["sha256"]) == 64
    assert manifest["sources"][0]["source"] == "file:client-upload.md"
    assert "digest_cost" not in manifest


def test_digest_uses_injected_client_and_truncates_sections() -> None:
    client = _FakeClient()
    records = [{"source": "file:fees.md", "text": "Fees are listed.", "status": "ok"}]

    digest, usage = asyncio.run(
        generate_digest(
            records,
            business_name="Example",
            max_chars=50,
            client=client,
            model="test-model",
        )
    )

    assert digest == "## Services\nSource-backed details."
    assert usage == {"input_tokens": 12, "output_tokens": 4}
    assert "### Source: file:fees.md" in client.aio.models.arguments["contents"]
    assert client.aio.models.arguments["config"] == {"response_mime_type": "text/plain"}


def test_chunking_and_section_boundary_truncation() -> None:
    assert chunk_text("abc def ghi", max_chars=7) == ["abc def", "ghi"]
    digest, truncated = truncate_digest_at_section_boundary(
        "## Services\nShort.\n\n## Fees\nLonger details.", 30
    )
    assert truncated is True
    assert digest == "## Services\nShort."


def test_crawler_checks_redirect_hops_before_fetching() -> None:
    requested: list[str] = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested.append(self.path)
            if self.path == "/robots.txt":
                body = b"User-agent: *\n"
                self.send_response(200)
                self.send_header("Content-Type", "text/plain")
            else:
                body = b""
                self.send_response(302)
                self.send_header("Location", "/private")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, _format: str, *_args) -> None:
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}/"
        records = crawl_websites(
            [root],
            request_delay=0,
            allow_url=lambda url: not url.endswith("/private"),
        )
        assert "/private" not in requested
        assert any(record["status"] == "error" for record in records)
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
