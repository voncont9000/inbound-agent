import asyncio
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException
from inbound_core.setup_draft import SetupDraft, apply_setup_draft
from inbound_core.templates import find_template, sample_setup_draft
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.auth import TenantContext, get_tenant_session, require_role
from inbound_api.jobs import enqueue
from inbound_api.models import Job, KnowledgeDigest, KnowledgeSource, MembershipRole
from inbound_api.routers.assistants import create_assistant
from inbound_api.schemas import AssistantCreate, AssistantResponse, JobResponse
from inbound_api.services.audit import write_audit
from inbound_api.services.url_safety import is_public_http_url, normalize_website_url
from inbound_api.settings import get_settings

router = APIRouter(tags=["assistants"])
_owner = Depends(require_role(MembershipRole.OWNER))
_tenant_session_dependency = Depends(get_tenant_session)


class AssistantSetupDraftCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    business_name: str = Field(min_length=1)
    website_url: str
    template: str

    @field_validator("website_url", mode="before")
    @classmethod
    def normalize_website_url_before_validation(cls, value: object) -> object:
        return normalize_website_url(value) if isinstance(value, str) else value


class AssistantSetupComplete(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    business_name: str = Field(min_length=1)
    template: str
    website_url: str | None = None
    accepted: SetupDraft = Field(default_factory=SetupDraft)
    use_sample_content: bool = False

    @field_validator("website_url", mode="before")
    @classmethod
    def normalize_optional_website_url_before_validation(cls, value: object) -> object:
        if not isinstance(value, str):
            return value
        return normalize_website_url(value) or None


def _template_config(template_name: str):
    settings = get_settings()
    template = find_template(settings.templates_dir, template_name)
    if template is None:
        raise HTTPException(status_code=422, detail="Unknown template")
    return template


async def _validate_website_url(url: str) -> None:
    parsed_url = urlsplit(url)
    if parsed_url.scheme.casefold() not in {"http", "https"} or not parsed_url.hostname:
        raise HTTPException(status_code=422, detail="Website URL must be HTTP(S) with a host")
    settings = get_settings()
    if not settings.knowledge_allow_private_urls and not await asyncio.to_thread(
        is_public_http_url, url
    ):
        raise HTTPException(status_code=422, detail="Website URL must resolve to public IPs")


@router.post("/assistant-setup/drafts", response_model=JobResponse, status_code=202)
async def create_setup_draft(
    payload: AssistantSetupDraftCreate,
    _: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> JobResponse:
    _template_config(payload.template)
    await _validate_website_url(payload.website_url)
    settings = get_settings()
    if not (settings.google_api_key or "").strip():
        raise HTTPException(status_code=503, detail="Website drafting is not configured.")
    job_id = await enqueue(
        session,
        "assistant_setup_draft",
        payload.model_dump(mode="json"),
    )
    job = await session.get(Job, job_id)
    return JobResponse.model_validate(job)


@router.post(
    "/assistant-setup/complete",
    response_model=AssistantResponse,
    status_code=201,
)
async def complete_setup(
    payload: AssistantSetupComplete,
    ctx: TenantContext = _owner,
    session: AsyncSession = _tenant_session_dependency,
) -> AssistantResponse:
    selected_template = _template_config(payload.template)
    template = selected_template.config
    if payload.website_url is not None:
        await _validate_website_url(payload.website_url)
    if payload.use_sample_content:
        if selected_template.meta.sample is None:
            raise HTTPException(status_code=422, detail="Template has no sample content")
        sample_draft = sample_setup_draft(selected_template.meta.sample)
        accepted_data = sample_draft.model_dump(mode="python")
        accepted_data.update(
            {
                section: value
                for section, value in payload.accepted.model_dump(mode="python").items()
                if value is not None
            }
        )
        accepted = SetupDraft.model_validate(accepted_data)
    else:
        accepted = payload.accepted
    config = apply_setup_draft(template, payload.business_name, accepted)
    assistant = await create_assistant(
        AssistantCreate(name=payload.name, template=payload.template, config=config),
        ctx,
        session,
    )
    if accepted.company_info:
        session.add(
            KnowledgeDigest(
                tenant_id=ctx.tenant_id,
                assistant_id=assistant.id,
                content=accepted.company_info,
                edited_by=ctx.user.id,
            )
        )
    if payload.website_url is not None:
        session.add(
            KnowledgeSource(
                tenant_id=ctx.tenant_id,
                assistant_id=assistant.id,
                kind="url",
                uri=payload.website_url,
                status="pending",
                manifest={},
            )
        )
        await session.flush()
        await enqueue(
            session,
            "knowledge_import",
            {"assistant_id": str(assistant.id)},
        )
    accepted_sections = [
        section
        for section, value in accepted.model_dump(mode="python").items()
        if value is not None
    ]
    await write_audit(
        session,
        ctx,
        "assistant.setup_completed",
        "assistant",
        assistant.id,
        {
            "accepted_sections": accepted_sections,
            "use_sample_content": payload.use_sample_content,
        },
    )
    return assistant
