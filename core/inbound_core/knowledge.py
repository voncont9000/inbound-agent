from __future__ import annotations

import hashlib
import logging
import re
import time
from datetime import UTC, datetime
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.error import HTTPError, URLError
from urllib.parse import urldefrag, urljoin, urlsplit, urlunsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener
from urllib.robotparser import RobotFileParser

import trafilatura
from docx import Document
from pypdf import PdfReader

LOGGER = logging.getLogger(__name__)
DEFAULT_EXTRACTION_MODEL = "gemini-3.8-flash"
USER_AGENT = "InboundAgentKnowledgeImporter/0.1"
REQUEST_DELAY_SECONDS = 0.5
MAX_DOCUMENT_CHARS = 400_000
CHUNK_CHARS = 1_200
DIGEST_PROMPT_TEMPLATE = (
    "You are preparing the reference sheet a telephone receptionist for {business_name} "
    "will use to answer callers.\n"
    "From the source documents below, write a concise Markdown reference of at most "
    "{max_chars} characters.\n"
    "Use these sections when the sources support them: Services offered; Services not "
    "offered; Prices and fees; Opening hours and locations; How the process works; "
    "Frequently asked questions; Other facts.\n"
    "Rules:\n"
    "- Include only facts stated in the sources. Do not infer, generalise or add advice.\n"
    "- Keep exact figures, names, addresses, phone numbers, email addresses and conditions "
    '(e.g. "plus VAT", "from").\n'
    "- After each fact or FAQ answer, cite its source in square brackets, "
    "e.g. [https://example.com/fees].\n"
    '- Where sources conflict, include both versions with their sources and write "CONFLICT".\n'
    "- Write short, plain sentences suitable for reading aloud.\n"
    "\nSources:\n{documents}\n"
)


class _PageParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.links: list[str] = []
        self.title_parts: list[str] = []
        self.in_title = False

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() == "title":
            self.in_title = True
        if tag.lower() == "a":
            href = dict(attrs).get("href")
            if href:
                self.links.append(href)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() == "title":
            self.in_title = False

    def handle_data(self, data: str) -> None:
        if self.in_title:
            self.title_parts.append(data)


class _SameHostRedirectHandler(HTTPRedirectHandler):
    def __init__(
        self,
        allowed_host: str,
        robots: RobotFileParser | None = None,
    ) -> None:
        super().__init__()
        self.allowed_host = allowed_host.casefold()
        self.robots = robots

    def redirect_request(
        self, request: Request, file: Any, code: int, message: str, headers: Any, new_url: str
    ) -> Request | None:
        if (urlsplit(new_url).hostname or "").casefold() != self.allowed_host:
            raise HTTPError(new_url, code, "Cross-host redirect blocked", headers, file)
        if self.robots and not self.robots.can_fetch(USER_AGENT, new_url):
            raise HTTPError(new_url, code, "Redirect disallowed by robots.txt", headers, file)
        return super().redirect_request(request, file, code, message, headers, new_url)


class _RequestPacer:
    def __init__(self, delay_seconds: float) -> None:
        self.delay_seconds = delay_seconds
        self.last_request = 0.0

    def wait(self) -> None:
        now = time.monotonic()
        if self.last_request:
            remaining = self.delay_seconds - (now - self.last_request)
            if remaining > 0:
                time.sleep(remaining)
        self.last_request = time.monotonic()


def _normalise_url(url: str) -> str:
    without_fragment = urldefrag(url)[0]
    parts = urlsplit(without_fragment)
    path = parts.path or "/"
    return urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path, parts.query, ""))


def _fetch(url: str, opener: Any, pacer: _RequestPacer) -> tuple[bytes, Any, str]:
    pacer.wait()
    request = Request(url, headers={"User-Agent": USER_AGENT})
    with opener.open(request, timeout=20) as response:
        return response.read(), response.headers, response.geturl()


def _robots_parser(start_url: str, opener: Any, pacer: _RequestPacer) -> RobotFileParser:
    parts = urlsplit(start_url)
    robots_url = urlunsplit((parts.scheme, parts.netloc, "/robots.txt", "", ""))
    parser = RobotFileParser(robots_url)
    try:
        body, _, _ = _fetch(robots_url, opener, pacer)
        parser.parse(body.decode("utf-8", errors="replace").splitlines())
    except HTTPError as error:
        if error.code == 404:
            parser.parse([])
        else:
            LOGGER.warning("Could not read robots.txt at %s: %s", robots_url, error)
            parser.parse(["User-agent: *", "Disallow: /"])
    except (OSError, URLError, TimeoutError) as error:
        LOGGER.warning("Could not read robots.txt at %s: %s", robots_url, error)
        parser.parse(["User-agent: *", "Disallow: /"])
    return parser


def _page_title_and_links(html: str) -> tuple[str, list[str]]:
    parser = _PageParser()
    parser.feed(html)
    return " ".join(" ".join(parser.title_parts).split()), parser.links


def _decode_html(body: bytes, headers: Any) -> str:
    charset = headers.get_content_charset() or "utf-8"
    return body.decode(charset, errors="replace")


def crawl_websites(
    urls: list[str],
    max_pages: int = 50,
    request_delay: float = REQUEST_DELAY_SECONDS,
) -> list[dict[str, Any]]:
    if max_pages < 1:
        raise ValueError("max_pages must be at least 1.")
    records: list[dict[str, Any]] = []
    pacer = _RequestPacer(request_delay)
    visited: set[str] = set()
    page_requests = 0

    for start_url in urls:
        if page_requests >= max_pages:
            break
        start = urlsplit(start_url)
        if start.scheme not in {"http", "https"} or not start.hostname:
            records.append(
                _source_record(start_url, "error", error="Only HTTP(S) URLs are allowed.")
            )
            continue
        allowed_host = start.hostname.casefold()
        opener = build_opener(_SameHostRedirectHandler(allowed_host))
        robots = _robots_parser(start_url, opener, pacer)
        opener = build_opener(_SameHostRedirectHandler(allowed_host, robots))
        queue = [_normalise_url(start_url)]

        while queue and page_requests < max_pages:
            url = queue.pop(0)
            if url in visited:
                continue
            visited.add(url)
            if not robots.can_fetch(USER_AGENT, url):
                records.append(
                    _source_record(url, "robots_disallowed", error="Disallowed by robots.txt.")
                )
                continue
            page_requests += 1
            try:
                body, headers, final_url = _fetch(url, opener, pacer)
                if (urlsplit(final_url).hostname or "").casefold() != allowed_host:
                    raise ValueError("Cross-host redirect blocked.")
                content_type = headers.get_content_type().lower()
                if content_type not in {"text/html", "application/xhtml+xml"}:
                    records.append(
                        _source_record(
                            url, "skipped_non_html", error=f"Content-Type: {content_type}"
                        )
                    )
                    continue
                html = _decode_html(body, headers)
                title, links = _page_title_and_links(html)
                try:
                    text = (
                        trafilatura.extract(
                            html,
                            include_comments=False,
                            include_tables=True,
                        )
                        or ""
                    )
                    status = "ok" if text.strip() else "empty"
                    records.append(_source_record(url, status, text=text, title=title))
                except Exception as error:
                    text = ""
                    records.append(_source_record(url, "error", title=title, error=str(error)))
                for href in links:
                    absolute = _normalise_url(urljoin(final_url, href))
                    parts = urlsplit(absolute)
                    if (
                        parts.scheme in {"http", "https"}
                        and (parts.hostname or "").casefold() == allowed_host
                        and absolute not in visited
                        and absolute not in queue
                    ):
                        queue.append(absolute)
            except Exception as error:
                records.append(
                    _source_record(url, "error", error=f"{type(error).__name__}: {error}")
                )

    return records


def _source_record(
    source: str,
    status: str,
    text: str = "",
    title: str = "",
    error: str | None = None,
) -> dict[str, Any]:
    return {
        "source": source,
        "title": title,
        "text": text,
        "fetched_at": datetime.now(UTC).isoformat(),
        "status": status,
        "chars": len(text),
        "errors": [error] if error else [],
    }


def read_document(path: str | Path) -> tuple[str, str]:
    document_path = Path(path)
    suffix = document_path.suffix.lower()
    if suffix in {".txt", ".md"}:
        return document_path.read_text(encoding="utf-8"), document_path.name
    if suffix in {".html", ".htm"}:
        html = document_path.read_text(encoding="utf-8")
        title, _ = _page_title_and_links(html)
        return trafilatura.extract(html, include_comments=False, include_tables=True) or "", (
            title or document_path.name
        )
    if suffix == ".pdf":
        reader = PdfReader(str(document_path))
        text = "\n".join(page.extract_text() or "" for page in reader.pages)
        return text, document_path.name
    if suffix == ".docx":
        document = Document(str(document_path))
        paragraphs = [paragraph.text for paragraph in document.paragraphs if paragraph.text]
        tables = [
            " | ".join(cell.text for cell in row.cells)
            for table in document.tables
            for row in table.rows
        ]
        return "\n".join((*paragraphs, *tables)), document_path.name
    raise ValueError(f"Unsupported knowledge file type: {suffix or '(no extension)'}")


def read_files(
    paths: list[str | Path], display_names: list[str] | None = None
) -> list[dict[str, Any]]:
    records = []
    for index, path in enumerate(paths):
        display_name = (
            display_names[index]
            if display_names and index < len(display_names)
            else Path(path).name
        )
        source = f"file:{display_name}"
        try:
            text, title = read_document(path)
            status = "ok" if text.strip() else "empty"
            records.append(_source_record(source, status, text=text, title=title))
        except Exception as error:
            records.append(
                _source_record(source, "error", error=f"{type(error).__name__}: {error}")
            )
    return records


def chunk_text(text: str, max_chars: int = CHUNK_CHARS) -> list[str]:
    if max_chars < 1:
        raise ValueError("max_chars must be at least 1.")
    remaining = " ".join(text.split())
    chunks = []
    while remaining:
        if len(remaining) <= max_chars:
            chunks.append(remaining)
            break
        split_at = remaining.rfind(" ", 0, max_chars + 1)
        if split_at <= 0:
            split_at = max_chars
        chunks.append(remaining[:split_at].strip())
        remaining = remaining[split_at:].strip()
    return chunks


def build_chunks(records: list[dict[str, Any]]) -> list[dict[str, Any]]:
    chunks = []
    for record in records:
        for index, text in enumerate(chunk_text(record["text"])):
            chunks.append(
                {
                    "source": record["source"],
                    "title": record.get("title", ""),
                    "chunk_index": index,
                    "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
                    "text": text,
                }
            )
    return chunks


def documents_for_prompt(records: list[dict[str, Any]]) -> str:
    documents = "\n\n".join(
        f"### Source: {record['source']}\n{record['text']}"
        for record in records
        if record["text"].strip()
    )
    return documents[:MAX_DOCUMENT_CHARS]


def build_digest_prompt(business_name: str, max_chars: int, documents: str) -> str:
    return DIGEST_PROMPT_TEMPLATE.format(
        business_name=business_name,
        max_chars=max_chars,
        documents=documents,
    )


def truncate_digest_at_section_boundary(text: str, max_chars: int) -> tuple[str, bool]:
    text = text.strip()
    if len(text) <= max_chars:
        return text, False
    sections = re.split(r"(?m)(?=^#{1,6}\s)", text)
    included = []
    length = 0
    for section in sections:
        if length + len(section) > max_chars:
            break
        included.append(section)
        length += len(section)
    return "".join(included).rstrip(), True


def response_usage(response: Any) -> dict[str, int]:
    metadata = getattr(response, "usage_metadata", None)
    return {
        "input_tokens": int(
            getattr(metadata, "prompt_token_count", 0) or getattr(metadata, "input_tokens", 0) or 0
        ),
        "output_tokens": int(
            getattr(metadata, "candidates_token_count", 0)
            or getattr(metadata, "output_tokens", 0)
            or 0
        ),
    }


async def generate_digest(
    records: list[dict[str, Any]],
    *,
    business_name: str,
    max_chars: int,
    client: Any,
    model: str,
) -> tuple[str, dict[str, int]]:
    response = await client.aio.models.generate_content(
        model=model,
        contents=build_digest_prompt(
            business_name,
            max_chars,
            documents_for_prompt(records),
        ),
        config={"response_mime_type": "text/plain"},
    )
    usage = response_usage(response)
    digest, truncated = truncate_digest_at_section_boundary(response.text or "", max_chars)
    if truncated:
        LOGGER.warning(
            "Knowledge digest exceeded %d characters and was truncated at a section boundary.",
            max_chars,
        )
    return digest, usage


def build_manifest(
    records: list[dict[str, Any]],
    *,
    business_name: str,
    model: str,
    max_digest_chars: int,
    usage: dict[str, int],
    cost: dict[str, Any] | None = None,
) -> dict[str, Any]:
    manifest = {
        "business_name": business_name,
        "model": model,
        "max_digest_chars": max_digest_chars,
        "sources": [
            {key: value for key, value in record.items() if key != "text"} for record in records
        ],
        "digest_usage": usage,
    }
    if cost is not None:
        manifest["digest_cost"] = cost
    return manifest
