import asyncio
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from inbound_core.config import AssistantConfig
from inbound_core.knowledge import (
    build_chunks,
    build_manifest,
    crawl_websites,
    generate_digest,
    read_files,
)
from sqlalchemy import delete, select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import PermanentJobError, register
from inbound_api.models import (
    Assistant,
    KnowledgeChunk,
    KnowledgeDigest,
    KnowledgeSource,
)
from inbound_api.settings import get_settings

LOGGER = logging.getLogger(__name__)


def make_genai_client(api_key: str):
    from google import genai

    return genai.Client(api_key=api_key)


@register("knowledge_import")
async def knowledge_import(session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    assistant_id = UUID(payload["assistant_id"])
    assistant = await session.get(Assistant, assistant_id)
    if assistant is None:
        raise PermanentJobError("Assistant not found")
    settings = get_settings()
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        raise PermanentJobError(
            "GOOGLE_API_KEY is not configured; cannot generate the knowledge digest."
        )
    config = AssistantConfig.model_validate(assistant.config)
    sources = list(
        await session.scalars(
            select(KnowledgeSource)
            .where(KnowledgeSource.assistant_id == assistant_id)
            .order_by(KnowledgeSource.created_at, KnowledgeSource.id)
        )
    )
    if not sources:
        raise PermanentJobError("No knowledge sources are available.")
    records_by_source: dict[UUID, list[dict[str, Any]]] = {}
    all_records: list[dict[str, Any]] = []
    for source in sources:
        if source.kind == "url":
            records = await asyncio.to_thread(
                crawl_websites,
                [source.uri],
                max_pages=settings.knowledge_max_pages,
                request_delay=settings.knowledge_request_delay,
            )
        else:
            stored_path = source.manifest.get("stored_path")
            if not stored_path:
                records = [
                    {
                        "source": f"file:{source.uri}",
                        "title": source.uri,
                        "text": "",
                        "fetched_at": datetime.now(UTC).isoformat(),
                        "status": "error",
                        "chars": 0,
                        "errors": ["Stored file path is missing."],
                    }
                ]
            else:
                file_path = settings.upload_dir / stored_path
                records = await asyncio.to_thread(
                    read_files, [file_path], display_names=[source.uri]
                )
        records_by_source[source.id] = records
        all_records.extend(records)
    client = make_genai_client(api_key)
    try:
        digest, usage = await generate_digest(
            all_records,
            business_name=config.business_name,
            max_chars=config.knowledge.max_digest_chars,
            client=client,
            model=settings.knowledge_model,
        )
    finally:
        await client.aio.aclose()
    total_chunks = 0
    now = datetime.now(UTC)
    for source in sources:
        records = records_by_source[source.id]
        await session.execute(delete(KnowledgeChunk).where(KnowledgeChunk.source_id == source.id))
        chunks = build_chunks(records)
        next_index = 0
        for chunk in chunks:
            session.add(
                KnowledgeChunk(
                    tenant_id=source.tenant_id,
                    source_id=source.id,
                    chunk_index=next_index,
                    title=chunk["title"],
                    text=chunk["text"],
                    sha256=chunk["sha256"],
                    embedding=None,
                )
            )
            next_index += 1
        total_chunks += next_index
        retained = {
            key: value
            for key, value in source.manifest.items()
            if key in {"stored_path", "filename", "content_type", "size"}
        }
        source.manifest = {
            **retained,
            "pages": [
                {key: value for key, value in record.items() if key != "text"} for record in records
            ],
            "chunk_count": next_index,
        }
        source.status = "ready" if any(record["status"] == "ok" for record in records) else "failed"
        source.fetched_at = now
    digest_row = KnowledgeDigest(
        tenant_id=assistant.tenant_id,
        assistant_id=assistant_id,
        content=digest,
        edited_by=None,
    )
    session.add(digest_row)
    await session.flush()
    manifest = build_manifest(
        all_records,
        business_name=config.business_name,
        model=settings.knowledge_model,
        max_digest_chars=config.knowledge.max_digest_chars,
        usage=usage,
    )
    return {
        "digest_id": str(digest_row.id),
        "digest_chars": len(digest),
        "chunk_count": total_chunks,
        "manifest": manifest,
    }
