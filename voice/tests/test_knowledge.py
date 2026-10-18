import asyncio
import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace

from docx import Document
from pypdf import PdfReader, PdfWriter
from pypdf.generic import DecodedStreamObject, DictionaryObject, NameObject

from inbound_voice.knowledge import (
    USER_AGENT,
    chunk_text,
    crawl_websites,
    import_knowledge,
    read_document,
    truncate_digest_at_section_boundary,
)
from inbound_voice.tools import DEFAULT_CONFIG_PATH


class _FakeModels:
    def __init__(self, response):
        self.response = response
        self.arguments = None

    async def generate_content(self, **arguments):
        self.arguments = arguments
        return self.response


class _FakeClient:
    def __init__(self, response):
        self.models = _FakeModels(response)
        self.aio = SimpleNamespace(models=self.models)


def _make_pdf(path: Path, text: str) -> None:
    writer = PdfWriter()
    page = writer.add_blank_page(width=300, height=300)
    font = writer._add_object(
        DictionaryObject(
            {
                NameObject("/Type"): NameObject("/Font"),
                NameObject("/Subtype"): NameObject("/Type1"),
                NameObject("/BaseFont"): NameObject("/Helvetica"),
            }
        )
    )
    page[NameObject("/Resources")] = DictionaryObject(
        {NameObject("/Font"): DictionaryObject({NameObject("/F1"): font})}
    )
    content = DecodedStreamObject()
    content.set_data(f"BT /F1 12 Tf 72 200 Td ({text}) Tj ET".encode())
    page[NameObject("/Contents")] = writer._add_object(content)
    with path.open("wb") as pdf:
        writer.write(pdf)


def test_crawler_obeys_robots_same_host_and_max_pages() -> None:
    requests = []
    user_agents = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            requests.append(self.path)
            user_agents.append(self.headers.get("User-Agent"))
            if self.path == "/robots.txt":
                body = b"User-agent: *\nDisallow: /blocked\n"
                content_type = "text/plain"
            elif self.path == "/":
                body = (
                    b"<html><head><title>Home</title></head><body><main>"
                    b"Home content. <a href='/one'>One</a>"
                    b"<a href='/blocked'>Blocked</a>"
                    b"<a href='http://example.invalid/outside'>Outside</a>"
                    b"<a href='/binary'>Binary</a>"
                    b"</main></body></html>"
                )
                content_type = "text/html"
            elif self.path == "/one":
                body = (
                    b"<html><head><title>One</title></head><body>"
                    b"<main>One page.</main></body></html>"
                )
                content_type = "text/html"
            elif self.path == "/blocked":
                body = b"blocked"
                content_type = "text/html"
            elif self.path == "/binary":
                body = b"not html"
                content_type = "application/pdf"
            else:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", content_type)
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, *_args):
            return

    server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        root = f"http://127.0.0.1:{server.server_port}/"
        limited = crawl_websites([root], max_pages=1, request_delay=0)
        assert [record["source"] for record in limited if record["status"] == "ok"] == [root]
        assert "/one" not in requests

        requests.clear()
        records = crawl_websites([root], max_pages=3, request_delay=0)
        assert {record["title"] for record in records if record["status"] == "ok"} == {
            "Home",
            "One",
        }
        assert any(record["status"] == "robots_disallowed" for record in records)
        assert any(record["status"] == "skipped_non_html" for record in records)
        assert "/blocked" not in requests
        assert all("example.invalid" not in record["source"] for record in records)
        assert all(agent == USER_AGENT for agent in user_agents)
        assert records[0]["text"]
        assert USER_AGENT == "InboundAgentKnowledgeImporter/0.1"
    finally:
        server.shutdown()
        server.server_close()
        thread.join(timeout=2)


def test_document_readers_support_markdown_docx_and_pdf(tmp_path) -> None:
    markdown = tmp_path / "fees.md"
    markdown.write_text("Markdown fee: £595 plus VAT.", encoding="utf-8")
    assert "£595" in read_document(markdown)[0]
    text_file = tmp_path / "notes.txt"
    text_file.write_text("Plain text source.", encoding="utf-8")
    assert read_document(text_file)[0] == "Plain text source."

    docx_path = tmp_path / "client-care.docx"
    document = Document()
    document.add_paragraph("Complaints are acknowledged within eight weeks.")
    document.save(docx_path)
    assert "eight weeks" in read_document(docx_path)[0]

    pdf_path = tmp_path / "fees.pdf"
    _make_pdf(pdf_path, "PDF fee details")
    assert "PDF fee details" in read_document(pdf_path)[0]
    assert PdfReader(str(pdf_path)).pages[0].extract_text()


def test_chunking_and_digest_truncate_at_section_boundary() -> None:
    chunks = chunk_text(("alpha " * 800).strip())
    assert len(chunks) > 1
    assert all(len(chunk) <= 1_200 for chunk in chunks)

    digest, truncated = truncate_digest_at_section_boundary(
        "## Services\nShort.\n\n## Prices\nThis is too long.", 30
    )
    assert truncated is True
    assert digest == "## Services\nShort."


def test_import_writes_chunks_manifest_digest_usage_and_cost(tmp_path) -> None:
    source = tmp_path / "details.md"
    source.write_text("Remortgage fixed fee is £595 plus VAT.", encoding="utf-8")
    response = SimpleNamespace(
        text="## Prices and fees\nRemortgage fixed fee is £595 plus VAT [file].",
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=20),
    )
    client = _FakeClient(response)
    output_dir = tmp_path / "knowledge"

    result = asyncio.run(
        import_knowledge(
            assistant_path=DEFAULT_CONFIG_PATH,
            urls=[],
            files=[source],
            output_dir=output_dir,
            client=client,
            model="gemini-3.8-flash",
        )
    )

    manifest = json.loads((output_dir / "manifest.json").read_text(encoding="utf-8"))
    chunks = [
        json.loads(line)
        for line in (output_dir / "chunks.jsonl").read_text(encoding="utf-8").splitlines()
    ]
    assert result["digest_chars"] == len(response.text)
    assert manifest["digest_usage"] == {"input_tokens": 100, "output_tokens": 20}
    assert manifest["digest_cost"]["currency"] == "USD"
    assert manifest["digest_cost"]["amount"] is not None
    assert manifest["sources"][0]["source"] == f"file:{source.name}"
    assert manifest["sources"][0]["chars"] == len("Remortgage fixed fee is £595 plus VAT.")
    assert manifest["sources"][0]["status"] == "ok"
    assert manifest["sources"][0]["fetched_at"]
    assert manifest["sources"][0]["errors"] == []
    assert str(tmp_path) not in json.dumps(manifest, ensure_ascii=False)
    assert chunks[0]["source"] == f"file:{source.name}"
    assert chunks[0]["title"] == source.name
    assert chunks[0]["chunk_index"] == 0
    assert len(chunks[0]["sha256"]) == 64
    assert "text/plain" == client.models.arguments["config"]["response_mime_type"]
    assert "response_json_schema" not in client.models.arguments["config"]
    assert f"### Source: file:{source.name}\n" in client.models.arguments["contents"]
    assert str(source) not in client.models.arguments["contents"]
    assert (output_dir / "digest.md").is_file()
