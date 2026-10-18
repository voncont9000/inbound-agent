from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
from pathlib import Path
from typing import Any

from inbound_core.knowledge import (
    CHUNK_CHARS,
    DEFAULT_EXTRACTION_MODEL,
    DIGEST_PROMPT_TEMPLATE,
    MAX_DOCUMENT_CHARS,
    REQUEST_DELAY_SECONDS,
    USER_AGENT,
    _decode_html,
    _fetch,
    _normalise_url,
    _page_title_and_links,
    _PageParser,
    _RequestPacer,
    _robots_parser,
    _SameHostRedirectHandler,
    _source_record,
    build_chunks,
    build_digest_prompt,
    build_manifest,
    chunk_text,
    crawl_websites,
    documents_for_prompt,
    generate_digest,
    read_document,
    read_files,
    response_usage,
    truncate_digest_at_section_boundary,
)

from inbound_voice.config import VOICE_ROOT, AssistantConfig, load_assistant_config
from inbound_voice.metrics import load_price_table

LOGGER = logging.getLogger(__name__)
PRICES_PATH = VOICE_ROOT / "config" / "prices.yaml"
_build_chunks = build_chunks
_documents_for_prompt = documents_for_prompt
_response_usage = response_usage

__all__ = [
    "CHUNK_CHARS",
    "DEFAULT_EXTRACTION_MODEL",
    "DIGEST_PROMPT_TEMPLATE",
    "MAX_DOCUMENT_CHARS",
    "PRICES_PATH",
    "REQUEST_DELAY_SECONDS",
    "USER_AGENT",
    "_PageParser",
    "_RequestPacer",
    "_SameHostRedirectHandler",
    "_build_chunks",
    "_decode_html",
    "_documents_for_prompt",
    "_fetch",
    "_normalise_url",
    "_page_title_and_links",
    "_response_usage",
    "_robots_parser",
    "_source_record",
    "_digest_cost",
    "_generate_digest",
    "_output_path",
    "_resolve_assistant_path",
    "build_chunks",
    "build_digest_prompt",
    "build_manifest",
    "chunk_text",
    "crawl_websites",
    "documents_for_prompt",
    "generate_digest",
    "import_knowledge",
    "main",
    "read_document",
    "read_files",
    "response_usage",
    "truncate_digest_at_section_boundary",
]


def _digest_cost(model: str, usage: dict[str, int]) -> dict[str, Any]:
    price_table = load_price_table(PRICES_PATH)
    model_prices = price_table.get("prices", {}).get("llm_models", {}).get(model)
    if not model_prices:
        return {"currency": "USD", "amount": None}
    input_rate = model_prices["input_per_1m_tokens"]["value"]
    output_rate = model_prices["output_per_1m_tokens"]["value"]
    amount = (usage["input_tokens"] * input_rate + usage["output_tokens"] * output_rate) / 1_000_000
    return {"currency": "USD", "amount": amount}


async def _generate_digest(
    records: list[dict[str, Any]],
    config: AssistantConfig,
    client: Any,
    model: str,
) -> tuple[str, dict[str, int], dict[str, Any]]:
    digest, usage = await generate_digest(
        records,
        business_name=config.business_name,
        max_chars=config.knowledge.max_digest_chars,
        client=client,
        model=model,
    )
    return digest, usage, _digest_cost(model, usage)


def _output_path(path: str | Path) -> Path:
    output = Path(path).expanduser()
    return output if output.is_absolute() else VOICE_ROOT / output


async def import_knowledge(
    assistant_path: str | Path,
    urls: list[str],
    files: list[str | Path],
    output_dir: str | Path,
    max_pages: int = 50,
    client: Any | None = None,
    model: str | None = None,
    request_delay: float = REQUEST_DELAY_SECONDS,
) -> dict[str, Any]:
    if not urls and not files:
        raise ValueError("Provide at least one --url or --file source.")
    config = load_assistant_config(assistant_path)
    records = crawl_websites(urls, max_pages=max_pages, request_delay=request_delay)
    records.extend(read_files(files))
    model_name = (model or os.environ.get("EXTRACTION_MODEL", DEFAULT_EXTRACTION_MODEL)).strip()
    if not model_name:
        raise ValueError("EXTRACTION_MODEL must not be blank.")
    owns_client = client is None
    if client is None:
        api_key = os.environ.get("GOOGLE_API_KEY", "").strip()
        if not api_key:
            raise ValueError("GOOGLE_API_KEY is required to generate the knowledge digest.")
        from google import genai

        client = genai.Client(api_key=api_key)
    try:
        digest, usage, cost = await _generate_digest(records, config, client, model_name)
    finally:
        if owns_client:
            await client.aio.aclose()

    destination = _output_path(output_dir)
    destination.mkdir(parents=True, exist_ok=True)
    (destination / "digest.md").write_text(digest.rstrip() + "\n", encoding="utf-8")
    with (destination / "chunks.jsonl").open("w", encoding="utf-8") as chunks_file:
        for chunk in build_chunks(records):
            chunks_file.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    manifest = build_manifest(
        records,
        business_name=config.business_name,
        model=model_name,
        max_digest_chars=config.knowledge.max_digest_chars,
        usage=usage,
        cost=cost,
    )
    (destination / "manifest.json").write_text(
        json.dumps(manifest, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "output_dir": str(destination),
        "digest_path": str(destination / "digest.md"),
        "digest_chars": len(digest),
        "source_count": len(records),
        "digest_usage": usage,
        "digest_cost": cost,
    }


def _resolve_assistant_path(path: str) -> Path:
    assistant = Path(path).expanduser()
    if not assistant.is_absolute():
        assistant = VOICE_ROOT / assistant
    return assistant


def main() -> None:
    parser = argparse.ArgumentParser(description="Import website and document knowledge.")
    subparsers = parser.add_subparsers(dest="command", required=True)
    import_parser = subparsers.add_parser("import")
    import_parser.add_argument("--assistant", required=True)
    import_parser.add_argument("--url", action="append", default=[])
    import_parser.add_argument("--file", action="append", default=[])
    import_parser.add_argument("--max-pages", type=int, default=50)
    import_parser.add_argument("--out")
    arguments = parser.parse_args()
    assistant_path = _resolve_assistant_path(arguments.assistant)
    output_dir = arguments.out or str(Path("knowledge") / assistant_path.stem)
    result = asyncio.run(
        import_knowledge(
            assistant_path=assistant_path,
            urls=arguments.url,
            files=arguments.file,
            output_dir=output_dir,
            max_pages=arguments.max_pages,
        )
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()
