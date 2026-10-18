from pathlib import Path
from urllib.parse import urlsplit
from uuid import UUID, uuid4

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.jobs import enqueue
from inbound_api.models import (
    Assistant,
    Job,
    KnowledgeDigest,
    KnowledgeGap,
    KnowledgeSource,
    MembershipRole,
)
from inbound_api.schemas import (
    JobResponse,
    KnowledgeDigestResponse,
    KnowledgeDigestUpdate,
    KnowledgeGapResponse,
    KnowledgeGapsResponse,
    KnowledgeGapUpdate,
    KnowledgeSourceCreate,
    KnowledgeSourceResponse,
    KnowledgeSourcesResponse,
)
from inbound_api.services.audit import write_audit
from inbound_api.services.pagination import page_query
from inbound_api.settings import get_settings

router = APIRouter(tags=["knowledge"])
_staff = Depends(require_role(MembershipRole.STAFF))
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


@router.get("/assistants/{assistant_id}/knowledge/sources", response_model=KnowledgeSourcesResponse)
async def list_sources(
    assistant_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> KnowledgeSourcesResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    rows = await session.scalars(
        select(KnowledgeSource)
        .where(KnowledgeSource.assistant_id == assistant_id)
        .order_by(KnowledgeSource.created_at.desc(), KnowledgeSource.id.desc())
    )
    return KnowledgeSourcesResponse(items=list(rows))


@router.post(
    "/assistants/{assistant_id}/knowledge/sources",
    response_model=KnowledgeSourceResponse,
    status_code=201,
    openapi_extra={
        "requestBody": {
            "required": True,
            "content": {
                "application/json": {"schema": KnowledgeSourceCreate.model_json_schema()},
                "multipart/form-data": {
                    "schema": {
                        "type": "object",
                        "properties": {"file": {"type": "string", "format": "binary"}},
                        "required": ["file"],
                    }
                },
            },
        }
    },
)
async def create_source(
    assistant_id: UUID,
    request: Request,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> KnowledgeSourceResponse:
    """Accept a URL JSON body or a document multipart upload."""
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    content_type = request.headers.get("content-type", "").casefold()
    if content_type.startswith("application/json"):
        try:
            source = KnowledgeSourceCreate.model_validate(await request.json())
        except Exception as error:
            raise HTTPException(status_code=422, detail=str(error)) from error
        parsed = urlsplit(source.uri)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            raise HTTPException(status_code=422, detail="Knowledge URL must be HTTP(S) with a host")
        row = KnowledgeSource(
            tenant_id=ctx.tenant_id,
            assistant_id=assistant_id,
            kind="url",
            uri=source.uri,
            status="pending",
            manifest={},
        )
    elif content_type.startswith("multipart/form-data"):
        form = await request.form()
        upload = form.get("file")
        if upload is None or not hasattr(upload, "read"):
            raise HTTPException(status_code=422, detail="Multipart field 'file' is required")
        original_name = str(getattr(upload, "filename", "") or "upload")
        filename = Path(original_name).name
        suffix = Path(filename).suffix.lower()
        if suffix not in {".txt", ".md", ".html", ".htm", ".pdf", ".docx"}:
            raise HTTPException(status_code=422, detail="Unsupported knowledge file type")
        contents = await upload.read(get_settings().max_upload_bytes + 1)
        if len(contents) > get_settings().max_upload_bytes:
            raise HTTPException(status_code=413, detail="Knowledge upload is too large")
        source_id = uuid4()
        stored_path = Path(str(ctx.tenant_id)) / str(assistant_id) / f"{source_id}{suffix}"
        destination = get_settings().upload_dir / stored_path
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(contents)
        row = KnowledgeSource(
            id=source_id,
            tenant_id=ctx.tenant_id,
            assistant_id=assistant_id,
            kind="file",
            uri=filename,
            status="pending",
            manifest={
                "stored_path": stored_path.as_posix(),
                "filename": filename,
                "content_type": getattr(upload, "content_type", None),
                "size": len(contents),
            },
        )
    else:
        raise HTTPException(status_code=422, detail="Expected JSON or multipart/form-data")
    session.add(row)
    await session.flush()
    return KnowledgeSourceResponse.model_validate(row)


@router.post(
    "/assistants/{assistant_id}/knowledge/import", response_model=JobResponse, status_code=202
)
async def import_knowledge(
    assistant_id: UUID,
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> JobResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    has_sources = await session.scalar(
        select(KnowledgeSource.id).where(KnowledgeSource.assistant_id == assistant_id).limit(1)
    )
    if has_sources is None:
        raise HTTPException(status_code=422, detail="Add at least one knowledge source first.")
    job_id = await enqueue(session, "knowledge_import", {"assistant_id": str(assistant_id)})
    job = await session.get(Job, job_id)
    return JobResponse.model_validate(job)


@router.get("/assistants/{assistant_id}/knowledge/digest", response_model=KnowledgeDigestResponse)
async def get_digest(
    assistant_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> KnowledgeDigestResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    digest = await session.scalar(
        select(KnowledgeDigest)
        .where(KnowledgeDigest.assistant_id == assistant_id)
        .order_by(KnowledgeDigest.created_at.desc(), KnowledgeDigest.id.desc())
        .limit(1)
    )
    if digest is None:
        raise HTTPException(status_code=404, detail="Knowledge digest not found")
    return KnowledgeDigestResponse.model_validate(digest)


@router.put("/assistants/{assistant_id}/knowledge/digest", response_model=KnowledgeDigestResponse)
async def update_digest(
    assistant_id: UUID,
    payload: KnowledgeDigestUpdate,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> KnowledgeDigestResponse:
    if await session.get(Assistant, assistant_id) is None:
        raise HTTPException(status_code=404, detail="Assistant not found")
    digest = KnowledgeDigest(
        tenant_id=ctx.tenant_id,
        assistant_id=assistant_id,
        content=payload.content,
        edited_by=ctx.user.id,
    )
    session.add(digest)
    await session.flush()
    await write_audit(
        session,
        ctx,
        "knowledge_digest.updated",
        "knowledge_digest",
        digest.id,
    )
    return KnowledgeDigestResponse.model_validate(digest)


@router.get("/knowledge-gaps", response_model=KnowledgeGapsResponse)
async def list_knowledge_gaps(
    status: str | None = None,
    assistant_id: UUID | None = None,
    cursor: str | None = None,
    limit: int = Query(50, ge=1, le=200),
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> KnowledgeGapsResponse:
    statement = select(KnowledgeGap)
    if status is not None:
        if status not in {"open", "answered", "dismissed"}:
            raise HTTPException(status_code=422, detail="Invalid knowledge-gap status")
        statement = statement.where(KnowledgeGap.status == status)
    if assistant_id is not None:
        statement = statement.where(KnowledgeGap.assistant_id == assistant_id)
    rows, next_cursor = await page_query(
        session,
        statement,
        created_at_column=KnowledgeGap.created_at,
        id_column=KnowledgeGap.id,
        cursor=cursor,
        limit=limit,
    )
    return KnowledgeGapsResponse(items=list(rows), next_cursor=next_cursor)


@router.patch("/knowledge-gaps/{gap_id}", response_model=KnowledgeGapResponse)
async def update_knowledge_gap(
    gap_id: UUID,
    payload: KnowledgeGapUpdate,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> KnowledgeGapResponse:
    gap = await session.get(KnowledgeGap, gap_id)
    if gap is None:
        raise HTTPException(status_code=404, detail="Knowledge gap not found")
    gap.status = payload.status
    return KnowledgeGapResponse.model_validate(gap)


@router.get("/jobs/{job_id}", response_model=JobResponse)
async def get_job(
    job_id: UUID,
    _: TenantContext = _staff,
    session: AsyncSession = _tenant_session_dependency,
) -> JobResponse:
    job = await session.get(Job, job_id)
    if job is None:
        raise HTTPException(status_code=404, detail="Job not found")
    return JobResponse.model_validate(job)
