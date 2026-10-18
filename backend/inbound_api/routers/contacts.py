import base64
import json
from datetime import datetime
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import or_, select

from inbound_api.auth import TenantContext, require_role
from inbound_api.db import tenant_session
from inbound_api.graph import (
    PersonAlreadyMerged,
    build_context_card,
    merge_persons,
    split_person,
    undo_last_merge,
)
from inbound_api.graph.normalize import normalize_phone
from inbound_api.models import (
    Assistant,
    Commitment,
    CommitmentStatus,
    Conversation,
    Fact,
    FactStatus,
    Identity,
    Intake,
    IntakeVersion,
    MembershipRole,
    Person,
    Task,
    TaskStatus,
)
from inbound_api.schemas import (
    CommitmentResponse,
    ContactDetailResponse,
    ContactMergeRequest,
    ContactMergeResponse,
    ContactSplitRequest,
    ContactSplitResponse,
    ContactsResponse,
    ContactSummary,
    ConversationSummary,
    FactResponse,
    IdentityResponse,
    TaskSummary,
)
from inbound_api.services.review import merged_needs_review

router = APIRouter(tags=["contacts"])
_staff = Depends(require_role(MembershipRole.STAFF))


def _decode_cursor(cursor: str) -> tuple[datetime, UUID]:
    try:
        padding = "=" * (-len(cursor) % 4)
        decoded = base64.urlsafe_b64decode(cursor + padding)
        value = json.loads(decoded)
        created_at = datetime.fromisoformat(value["created_at"])
        contact_id = UUID(value["id"])
        if created_at.tzinfo is None:
            raise ValueError
        return created_at, contact_id
    except (ValueError, TypeError, KeyError, json.JSONDecodeError) as error:
        raise HTTPException(status_code=400, detail="Invalid cursor") from error


def _encode_cursor(person: Person) -> str:
    value = json.dumps(
        {"created_at": person.created_at.isoformat(), "id": str(person.id)},
        separators=(",", ":"),
    ).encode()
    return base64.urlsafe_b64encode(value).decode().rstrip("=")


def _contact_summary(person: Person) -> ContactSummary:
    return ContactSummary(
        id=person.id,
        display_name=person.display_name,
        merged_into_id=person.merged_into_id,
        created_at=person.created_at,
        updated_at=person.updated_at,
    )


def _conversation_summary(
    conversation: Conversation,
    contact_name: str | None = None,
    needs_review: bool = False,
) -> ConversationSummary:
    return ConversationSummary(
        id=conversation.id,
        assistant_id=conversation.assistant_id,
        person_id=conversation.person_id,
        channel=conversation.channel.value,
        external_ref=conversation.external_ref,
        status=conversation.status.value,
        started_at=conversation.started_at,
        ended_at=conversation.ended_at,
        summary=conversation.summary,
        end_reason=conversation.end_reason,
        needs_review=needs_review,
        contact_name=contact_name,
    )


def _task_summary(task: Task) -> TaskSummary:
    return TaskSummary(
        id=task.id,
        kind=task.kind,
        status=task.status.value,
        due_at=task.due_at,
        payload=task.payload,
        source=task.source.value,
        conversation_id=task.conversation_id,
        person_id=task.person_id,
    )


@router.get("/contacts", response_model=ContactsResponse)
async def list_contacts(
    ctx: TenantContext = _staff,
    q: str | None = None,
    include_merged: bool = False,
    limit: int = Query(50, ge=1, le=200),
    cursor: str | None = None,
) -> ContactsResponse:
    async with tenant_session(ctx.tenant_id) as session:
        statement = select(Person)
        if not include_merged:
            statement = statement.where(Person.merged_into_id.is_(None))
        if q:
            escaped = q.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            pattern = f"%{escaped}%"
            identity_match = (
                select(Identity.id)
                .where(
                    Identity.person_id == Person.id,
                    Identity.value_normalized.ilike(pattern, escape="\\"),
                )
                .exists()
            )
            alternatives = [Person.display_name.ilike(pattern, escape="\\"), identity_match]
            normalized_phone = normalize_phone(q)
            if normalized_phone:
                alternatives.append(
                    select(Identity.id)
                    .where(
                        Identity.person_id == Person.id,
                        Identity.value_normalized == normalized_phone,
                    )
                    .exists()
                )
            statement = statement.where(or_(*alternatives))
        if cursor:
            created_at, contact_id = _decode_cursor(cursor)
            statement = statement.where(
                or_(
                    Person.created_at < created_at,
                    (Person.created_at == created_at) & (Person.id < contact_id),
                )
            )
        people = list(
            (
                await session.scalars(
                    statement.order_by(Person.created_at.desc(), Person.id.desc()).limit(limit + 1)
                )
            ).all()
        )
    has_more = len(people) > limit
    page = people[:limit]
    return ContactsResponse(
        items=[_contact_summary(person) for person in page],
        next_cursor=_encode_cursor(page[-1]) if has_more and page else None,
    )


@router.get("/contacts/{contact_id}", response_model=ContactDetailResponse)
async def get_contact(
    contact_id: UUID,
    ctx: TenantContext = _staff,
) -> ContactDetailResponse:
    async with tenant_session(ctx.tenant_id) as session:
        person = await session.scalar(select(Person).where(Person.id == contact_id))
        if person is None:
            raise HTTPException(status_code=404, detail="Contact not found")
        identities = (
            await session.scalars(
                select(Identity)
                .where(Identity.person_id == person.id)
                .order_by(Identity.type, Identity.value_normalized)
            )
        ).all()
        facts = (
            await session.scalars(
                select(Fact)
                .where(
                    Fact.subject_type == "person",
                    Fact.subject_id == person.id,
                    Fact.status == FactStatus.ACTIVE,
                )
                .order_by(Fact.valid_from.desc(), Fact.recorded_at.desc())
            )
        ).all()
        conversations = (
            await session.scalars(
                select(Conversation)
                .where(Conversation.person_id == person.id)
                .order_by(Conversation.started_at.desc(), Conversation.id.desc())
                .limit(20)
            )
        ).all()
        review_states: dict[UUID, bool] = {}
        if conversations:
            review_rows = await session.execute(
                select(Intake.conversation_id, Intake.data, Assistant.config)
                .join(Conversation, Conversation.id == Intake.conversation_id)
                .join(Assistant, Assistant.id == Conversation.assistant_id)
                .where(
                    Intake.conversation_id.in_([conversation.id for conversation in conversations]),
                    Intake.version == IntakeVersion.MERGED,
                )
            )
            review_states = {
                conversation_id: merged_needs_review(
                    data,
                    config.get("review_confidence_threshold", 0.7),
                )
                for conversation_id, data, config in review_rows.all()
            }
        tasks = (
            await session.scalars(
                select(Task)
                .where(Task.person_id == person.id, Task.status == TaskStatus.OPEN)
                .order_by(Task.due_at.asc().nullslast(), Task.created_at.desc())
            )
        ).all()
        commitments = (
            await session.scalars(
                select(Commitment)
                .where(
                    Commitment.person_id == person.id,
                    Commitment.status == CommitmentStatus.OPEN,
                )
                .order_by(Commitment.due_at.asc().nullslast(), Commitment.created_at.desc())
            )
        ).all()
        context_card = await build_context_card(session, person.id)
        return ContactDetailResponse(
            **_contact_summary(person).model_dump(),
            identities=[
                IdentityResponse(
                    id=identity.id,
                    type=identity.type.value,
                    value_normalized=identity.value_normalized,
                    verified=identity.verified,
                    confidence=identity.confidence,
                    source=identity.source,
                )
                for identity in identities
            ],
            active_facts=[
                FactResponse(
                    id=fact.id,
                    predicate=fact.predicate,
                    value=fact.value_json,
                    valid_from=fact.valid_from,
                    valid_to=fact.valid_to,
                    evidence=fact.evidence,
                    confidence=fact.confidence,
                    status=fact.status.value,
                )
                for fact in facts
            ],
            conversations=[
                _conversation_summary(
                    conversation,
                    person.display_name,
                    review_states.get(conversation.id, False),
                )
                for conversation in conversations
            ],
            open_tasks=[_task_summary(task) for task in tasks],
            open_commitments=[
                CommitmentResponse(
                    id=commitment.id,
                    what=commitment.what,
                    due_at=commitment.due_at,
                    status=commitment.status.value,
                )
                for commitment in commitments
            ],
            context_card=context_card,
        )


@router.post("/contacts/{contact_id}/merge", response_model=ContactMergeResponse)
async def merge_contact(
    contact_id: UUID,
    payload: ContactMergeRequest,
    ctx: TenantContext = _staff,
) -> ContactMergeResponse:
    async with tenant_session(ctx.tenant_id) as session:
        try:
            await merge_persons(
                session,
                survivor_id=contact_id,
                merged_id=payload.other_id,
                actor_user_id=ctx.user.id,
            )
        except PersonAlreadyMerged as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return ContactMergeResponse(survivor_id=contact_id, merged_id=payload.other_id)


@router.post("/contacts/{contact_id}/merge/undo", response_model=ContactMergeResponse)
async def undo_contact_merge(
    contact_id: UUID,
    ctx: TenantContext = _staff,
) -> ContactMergeResponse:
    async with tenant_session(ctx.tenant_id) as session:
        try:
            merged_id = await undo_last_merge(
                session,
                person_id=contact_id,
                actor_user_id=ctx.user.id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
    return ContactMergeResponse(survivor_id=contact_id, merged_id=merged_id)


@router.post("/contacts/{contact_id}/split", response_model=ContactSplitResponse)
async def split_contact(
    contact_id: UUID,
    payload: ContactSplitRequest,
    ctx: TenantContext = _staff,
) -> ContactSplitResponse:
    async with tenant_session(ctx.tenant_id) as session:
        try:
            split_person_id = await split_person(
                session,
                person_id=contact_id,
                identity_ids=payload.identity_ids,
                actor_user_id=ctx.user.id,
            )
        except LookupError as error:
            raise HTTPException(status_code=404, detail=str(error)) from error
        except ValueError as error:
            raise HTTPException(status_code=400, detail=str(error)) from error
    return ContactSplitResponse(person_id=contact_id, split_person_id=split_person_id)
