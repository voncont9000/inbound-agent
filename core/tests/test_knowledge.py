import asyncio
import ipaddress
import socket
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.error import URLError
from urllib.parse import urlsplit
from urllib.request import HTTPSHandler, Request

import pytest

from inbound_core.knowledge import (
    _fetch_checked,
    _PinnedHTTPConnection,
    _PinnedHTTPSConnection,
    _PinnedHTTPSHandler,
    _RequestPacer,
    _source_record,
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


def test_source_record_removes_c0_controls_except_line_whitespace() -> None:
    record = _source_record(
        "https://example.test",
        "ok",
        text="Fee\x00 details\x01\tand\r\nhours",
        title="Source\x02 title",
    )

    assert record["text"] == "Fee details\tand\r\nhours"
    assert record["title"] == "Source title"
    assert record["chars"] == len(record["text"])


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


def _address_info(address: str, port: int) -> tuple:
    return (socket.AF_INET, socket.SOCK_STREAM, socket.IPPROTO_TCP, "", (address, port))


def test_crawler_rejects_dns_rebinding_at_connect_time(monkeypatch: pytest.MonkeyPatch) -> None:
    resolutions = iter(("93.184.216.34", "127.0.0.1"))
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port: [_address_info(next(resolutions), port)],
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("blocked DNS answer must not connect"),
    )

    def allow_url(url: str) -> bool:
        host = urlsplit(url).hostname
        return all(
            ipaddress.ip_address(item[4][0]).is_global for item in socket.getaddrinfo(host, 80)
        )

    with pytest.raises(URLError):
        _fetch_checked(
            "http://crawler.example/",
            allowed_host="crawler.example",
            robots=None,
            pacer=_RequestPacer(0),
            allow_url=allow_url,
            allow_address=lambda address: address.is_global,
        )


def test_pinned_connection_rejects_mixed_public_and_private_dns(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port: [
            _address_info("93.184.216.34", port),
            _address_info("127.0.0.1", port),
        ],
    )
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda *_args, **_kwargs: pytest.fail("mixed DNS answers must not connect"),
    )

    connection = _PinnedHTTPConnection(
        "crawler.example",
        80,
        allow_address=lambda address: address.is_global,
    )
    with pytest.raises(OSError, match="address safety policy"):
        connection.connect()


def test_pinned_connection_connects_to_the_validated_public_address(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port: [
            _address_info("93.184.216.34", port),
            _address_info("1.1.1.1", port),
        ],
    )
    connected: list[tuple[str, int]] = []
    monkeypatch.setattr(
        socket,
        "create_connection",
        lambda address, _timeout: connected.append(address) or object(),
    )

    connection = _PinnedHTTPConnection(
        "crawler.example",
        80,
        allow_address=lambda address: address.is_global,
    )
    connection.connect()

    assert connected == [("93.184.216.34", 80)]


def test_pinned_https_connection_preserves_hostname_for_sni(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda _host, port: [_address_info("93.184.216.34", port)],
    )
    monkeypatch.setattr(socket, "create_connection", lambda *_args: "validated-socket")

    class FakeContext:
        hostname: str | None = None

        def wrap_socket(self, sock: object, *, server_hostname: str) -> str:
            assert sock == "validated-socket"
            self.hostname = server_hostname
            return "wrapped-socket"

    context = FakeContext()
    connection = _PinnedHTTPSConnection(
        "crawler.example",
        443,
        context=context,
        allow_address=lambda address: address.is_global,
    )
    connection.connect()

    assert context.hostname == "crawler.example"
    assert connection.sock == "wrapped-socket"


def test_pinned_https_handler_uses_context_without_check_hostname(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delattr(HTTPSHandler, "_check_hostname", raising=False)
    handler = _PinnedHTTPSHandler(lambda address: address.is_global)
    handler.__dict__.pop("_check_hostname", None)
    sentinel = object()
    calls: list[tuple[tuple, dict]] = []

    def do_open(*args, **kwargs):
        calls.append((args, kwargs))
        return sentinel

    monkeypatch.setattr(handler, "do_open", do_open)

    result = handler.https_open(Request("https://example.com/"))

    assert result is sentinel
    assert len(calls) == 1
    assert calls[0][1] == {"context": handler._context}
