"""Tenant-scoped customer identity, facts, and conversation context."""

import json
import logging
from datetime import UTC, datetime
from typing import Any
from uuid import UUID

from sqlalchemy import and_, or_, select, text, update
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.graph.normalize import normalize_email, normalize_phone
from inbound_api.models import (
    Assistant,
    AuditLog,
    Commitment,
    CommitmentStatus,
    Conversation,
    ConversationChannel,
    Edge,
    Fact,
    FactStatus,
    Identity,
    IdentityMergeAction,
    IdentityMergeLog,
    IdentityType,
    Intake,
    IntakeVersion,
    Message,
    MessageRole,
    Person,
    Task,
    TaskStatus,
)

logger = logging.getLogger(__name__)


class PersonAlreadyMerged(ValueError):
    """Raised when a merge participant has already been merged."""


def _intake_summary(data: dict[str, Any] | None) -> str | None:
    if not isinstance(data, dict):
        return None
    parts = []
    service = data.get("service")
    if isinstance(service, str) and service.strip():
        parts.append(service.strip())
    field_count = 0
    fields = data.get("fields", {})
    if isinstance(fields, dict):
        for name, entry in sorted(fields.items()):
            if not isinstance(entry, dict) or entry.get("value") is None:
                continue
            confidence = entry.get("confidence", 1.0 if entry.get("status") == "human" else 0.0)
            try:
                high_confidence = float(confidence) >= 0.7
            except (TypeError, ValueError):
                high_confidence = False
            if not high_confidence:
                continue
            value = " ".join(str(entry["value"]).split())
            if value:
                parts.append(f"{name}: {value}")
                field_count += 1
            if field_count >= 3:
                break
    return "; ".join(parts) or None


async def _tenant_id(session: AsyncSession) -> UUID:
    value = await session.scalar(text("SELECT current_setting('app.tenant_id', true)"))
    if not value:
        raise RuntimeError("Tenant context is required for graph inserts")
    return UUID(str(value))


async def _follow_person(session: AsyncSession, person: Person) -> Person:
    current = person
    visited = {current.id}
    while current.merged_into_id is not None:
        if current.merged_into_id in visited:
            break
        next_person = await session.scalar(
            select(Person).where(Person.id == current.merged_into_id)
        )
        if next_person is None:
            break
        current = next_person
        if current.id in visited:
            break
        visited.add(current.id)
    return current


async def _identity_person(
    session: AsyncSession, identity_type: IdentityType, normalized: str | None
) -> Person | None:
    if normalized is None:
        return None
    identity = await session.scalar(
        select(Identity).where(
            Identity.type == identity_type,
            Identity.value_normalized == normalized,
        )
    )
    if identity is None:
        return None
    person = await session.scalar(select(Person).where(Person.id == identity.person_id))
    if person is None:
        return None
    return await _follow_person(session, person)


async def resolve_person(
    session: AsyncSession,
    *,
    phone: str | None,
    email: str | None,
    whatsapp_id: str | None = None,
    source: str,
) -> UUID | None:
    """Resolve an existing person from deterministic channel identities."""
    phone_normalized = normalize_phone(phone)
    email_normalized = normalize_email(email)
    phone_person = await _identity_person(session, IdentityType.PHONE, phone_normalized)
    email_person = await _identity_person(session, IdentityType.EMAIL, email_normalized)
    whatsapp_person = await _identity_person(session, IdentityType.WHATSAPP, whatsapp_id)

    if (
        phone_person is not None
        and email_person is not None
        and phone_person.id != email_person.id
        and phone_normalized is not None
        and email_normalized is not None
    ):
        duplicate = await session.scalar(
            select(Fact.id).where(
                Fact.predicate == "possible_same_person",
                Fact.status == FactStatus.ACTIVE,
                Fact.subject_type == "person",
                Fact.object_type == "person",
                or_(
                    and_(
                        Fact.subject_id == phone_person.id,
                        Fact.object_id == email_person.id,
                    ),
                    and_(
                        Fact.subject_id == email_person.id,
                        Fact.object_id == phone_person.id,
                    ),
                ),
            )
        )
        if duplicate is None:
            tenant_id = await _tenant_id(session)
            session.add(
                Fact(
                    tenant_id=tenant_id,
                    subject_type="person",
                    subject_id=phone_person.id,
                    predicate="possible_same_person",
                    object_type="person",
                    object_id=email_person.id,
                    value_json={
                        "phone": phone_normalized,
                        "email": email_normalized,
                        "source": source,
                    },
                    valid_from=datetime.now(UTC),
                    evidence="Phone and email identities belong to different people",
                    confidence=0.5,
                    status=FactStatus.ACTIVE,
                )
            )
    if phone_person is not None:
        return phone_person.id
    if whatsapp_person is not None:
        return whatsapp_person.id
    if email_person is not None:
        return email_person.id
    return None


def _nonempty(value: Any) -> str | None:
    if value is None:
        return None
    text_value = str(value).strip()
    return text_value or None


def _field_value(fields: dict[str, Any], name: str) -> Any:
    entry = fields.get(name)
    if isinstance(entry, dict):
        return entry.get("value")
    return entry


def _field_entry(fields: dict[str, Any], name: str) -> dict[str, Any]:
    value = fields.get(name)
    return value if isinstance(value, dict) else {"value": value}


def _field_by_type(
    fields: dict[str, Any], definitions: list[dict[str, Any]], field_type: str, fallback: str
) -> str | None:
    for definition in definitions:
        if not isinstance(definition, dict):
            continue
        if definition.get("type") == field_type:
            name = definition.get("name")
            if name in fields:
                return name
    return fallback if fallback in fields else None


def _display_name(fields: dict[str, Any], phone: str | None, email: str | None) -> tuple[str, bool]:
    for name in ("caller_name", "name", "full_name", "contact_name", "customer_name"):
        value = _nonempty(_field_value(fields, name))
        if value:
            return value, True
    return phone or email or "Unknown caller", False


def _is_fallback_name(
    display_name: str, phone: str | None, email: str | None, raw_phone: str | None
) -> bool:
    candidates = {"Unknown caller", phone, email, raw_phone}
    return display_name in candidates


def _caller_id_field_value(fields: dict[str, Any], phone_field: str | None) -> Any:
    if phone_field is None:
        return None
    sources = _field_entry(fields, phone_field).get("sources", {})
    if not isinstance(sources, dict):
        return None
    caller_id = sources.get("caller_id", {})
    return caller_id.get("value") if isinstance(caller_id, dict) else None


async def _ensure_identity(
    session: AsyncSession,
    *,
    tenant_id: UUID,
    person_id: UUID,
    identity_type: IdentityType,
    value: str | None,
    confidence: float,
    conversation: Conversation,
) -> None:
    if value is None:
        return
    await session.execute(
        insert(Identity)
        .values(
            tenant_id=tenant_id,
            person_id=person_id,
            type=identity_type,
            value_normalized=value,
            verified=False,
            confidence=confidence,
            source=("whatsapp" if identity_type == IdentityType.WHATSAPP else "voice_call"),
        )
        .on_conflict_do_nothing(
            index_elements=[Identity.tenant_id, Identity.type, Identity.value_normalized]
        )
    )
    identity = await session.scalar(
        select(Identity).where(
            Identity.type == identity_type,
            Identity.value_normalized == value,
            Identity.person_id == person_id,
        )
    )
    if identity is None:
        return
    edge = await session.scalar(
        select(Edge.src_id).where(
            Edge.src_type == "conversation",
            Edge.src_id == conversation.id,
            Edge.rel == "via_identity",
            Edge.dst_type == "identity",
            Edge.dst_id == identity.id,
        )
    )
    if edge is None:
        session.add(
            Edge(
                tenant_id=tenant_id,
                src_type="conversation",
                src_id=conversation.id,
                rel="via_identity",
                dst_type="identity",
                dst_id=identity.id,
                valid_from=conversation.started_at,
            )
        )


async def ingest_conversation(session: AsyncSession, conversation_id: UUID) -> None:
    """Link a conversation to a person and persist merged intake facts."""
    tenant_id = await _tenant_id(session)
    conversation = await session.scalar(
        select(Conversation).where(Conversation.id == conversation_id)
    )
    if conversation is None:
        raise LookupError("Conversation not found")

    merged_intake = await session.scalar(
        select(Intake).where(
            Intake.conversation_id == conversation.id,
            Intake.version == IntakeVersion.MERGED,
        )
    )
    merged = merged_intake.data if merged_intake is not None else {}
    merged = merged if isinstance(merged, dict) else {}
    fields = merged.get("fields", {})
    fields = fields if isinstance(fields, dict) else {}
    live_intake = await session.scalar(
        select(Intake).where(
            Intake.conversation_id == conversation.id,
            Intake.version == IntakeVersion.LIVE,
        )
    )
    live_data = live_intake.data if live_intake is not None else {}
    live_data = live_data if isinstance(live_data, dict) else {}
    assistant = await session.scalar(
        select(Assistant).where(Assistant.id == conversation.assistant_id)
    )
    config = assistant.config if assistant and isinstance(assistant.config, dict) else {}
    service = merged.get("service")
    services = config.get("services", {})
    service_config = services.get(service, {}) if isinstance(services, dict) else {}
    definitions = service_config.get("fields", []) if isinstance(service_config, dict) else []
    definitions = definitions if isinstance(definitions, list) else []
    phone_field = _field_by_type(fields, definitions, "phone", "phone")
    email_field = _field_by_type(fields, definitions, "email", "email")

    raw_phone = next(
        (
            value
            for value in (
                (conversation.metrics or {}).get("from_number"),
                (conversation.metrics or {}).get("caller_id"),
                live_data.get("caller_id"),
                live_data.get("from_number"),
                _caller_id_field_value(fields, phone_field),
                _field_value(fields, phone_field) if phone_field else None,
            )
            if _nonempty(value)
        ),
        None,
    )
    normalized_phone = normalize_phone(_nonempty(raw_phone))
    whatsapp_id = (conversation.metrics or {}).get("whatsapp_wa_id")
    whatsapp_id = whatsapp_id if isinstance(whatsapp_id, str) and whatsapp_id else None
    email_value = _field_value(fields, email_field) if email_field else None
    normalized_email = normalize_email(_nonempty(email_value))
    display_name, name_is_known = _display_name(fields, normalized_phone, normalized_email)

    caller_id_phone = any(
        _nonempty(value)
        for value in (
            (conversation.metrics or {}).get("from_number"),
            (conversation.metrics or {}).get("caller_id"),
            live_data.get("caller_id"),
            live_data.get("from_number"),
            _caller_id_field_value(fields, phone_field),
        )
    )
    phone_entry = _field_entry(fields, phone_field) if phone_field else {}
    try:
        phone_confidence = 1.0 if caller_id_phone else float(phone_entry.get("confidence", 0))
    except (TypeError, ValueError):
        phone_confidence = 0.0
    email_entry = _field_entry(fields, email_field) if email_field else {}
    try:
        email_confidence = float(email_entry.get("confidence", 0))
    except (TypeError, ValueError):
        email_confidence = 0.0

    if conversation.person_id is not None:
        person = await session.scalar(select(Person).where(Person.id == conversation.person_id))
        if person is None:
            raise LookupError("Conversation person not found")
        person = await _follow_person(session, person)
    else:
        resolved_person_id = await resolve_person(
            session,
            phone=normalized_phone,
            email=normalized_email,
            whatsapp_id=whatsapp_id,
            source=(
                "whatsapp" if conversation.channel == ConversationChannel.WHATSAPP else "voice_call"
            ),
        )
        person = (
            await session.scalar(select(Person).where(Person.id == resolved_person_id))
            if resolved_person_id
            else None
        )
        if person is None:
            person = Person(
                tenant_id=tenant_id,
                display_name=display_name,
            )
            session.add(person)
            await session.flush()

    if name_is_known and _is_fallback_name(
        person.display_name, normalized_phone, normalized_email, _nonempty(raw_phone)
    ):
        person.display_name = display_name
    conversation.person_id = person.id
    await session.execute(
        update(Task)
        .where(Task.conversation_id == conversation.id, Task.person_id.is_(None))
        .values(person_id=person.id)
    )
    await _ensure_identity(
        session,
        tenant_id=tenant_id,
        person_id=person.id,
        identity_type=IdentityType.PHONE,
        value=normalized_phone,
        confidence=phone_confidence,
        conversation=conversation,
    )
    await _ensure_identity(
        session,
        tenant_id=tenant_id,
        person_id=person.id,
        identity_type=IdentityType.WHATSAPP,
        value=whatsapp_id,
        confidence=1.0,
        conversation=conversation,
    )
    await _ensure_identity(
        session,
        tenant_id=tenant_id,
        person_id=person.id,
        identity_type=IdentityType.EMAIL,
        value=normalized_email,
        confidence=email_confidence,
        conversation=conversation,
    )

    if merged_intake is not None:
        valid_from = conversation.ended_at or conversation.started_at
        for predicate, raw_entry in fields.items():
            entry = raw_entry if isinstance(raw_entry, dict) else {"value": raw_entry}
            value = entry.get("value")
            if value is None:
                continue
            already_ingested = await session.scalar(
                select(Fact.id).where(
                    Fact.subject_type == "person",
                    Fact.subject_id == person.id,
                    Fact.predicate == predicate,
                    Fact.source_conversation_id == conversation.id,
                )
            )
            if already_ingested is not None:
                continue
            active_fact = await session.scalar(
                select(Fact)
                .where(
                    Fact.subject_type == "person",
                    Fact.subject_id == person.id,
                    Fact.predicate == predicate,
                    Fact.status == FactStatus.ACTIVE,
                )
                .order_by(Fact.valid_from.desc(), Fact.recorded_at.desc())
                .limit(1)
            )
            if active_fact is not None and active_fact.value_json == value:
                continue
            sources = entry.get("sources", {})
            sources = sources if isinstance(sources, dict) else {}
            post_call_source = sources.get("post_call", {})
            source_evidence = (
                _nonempty(post_call_source.get("evidence"))
                if isinstance(post_call_source, dict)
                else None
            )
            evidence = source_evidence or (
                "Confirmed by caller during call"
                if entry.get("confirmed_by_caller")
                else f"merge status: {entry.get('status', 'unknown')}"
            )
            live_source = sources.get("live", {})
            caller_confirmed = bool(entry.get("confirmed_by_caller")) or bool(
                isinstance(live_source, dict) and live_source.get("confirmed_at")
            )
            try:
                confidence = 1.0 if caller_confirmed else float(entry.get("confidence", 0))
            except (TypeError, ValueError):
                confidence = 0.0
            status = FactStatus.ACTIVE
            valid_to = None
            superseded_at = None
            if active_fact is not None and active_fact.confidence == 1.0 and not caller_confirmed:
                status = FactStatus.REJECTED
                evidence = f"Not applied: conflicts with caller-confirmed value. {evidence}"
            elif active_fact is not None and valid_from < active_fact.valid_from:
                status = FactStatus.SUPERSEDED
                valid_to = active_fact.valid_from
                superseded_at = datetime.now(UTC)
            elif active_fact is not None:
                active_fact.valid_to = valid_from
                active_fact.superseded_at = datetime.now(UTC)
                active_fact.status = FactStatus.SUPERSEDED
            session.add(
                Fact(
                    tenant_id=tenant_id,
                    subject_type="person",
                    subject_id=person.id,
                    predicate=predicate,
                    value_json=value,
                    source_conversation_id=conversation.id,
                    valid_from=valid_from,
                    valid_to=valid_to,
                    superseded_at=superseded_at,
                    evidence=evidence,
                    confidence=confidence,
                    status=status,
                )
            )

    callback_tasks = (
        await session.scalars(
            select(Task).where(
                Task.conversation_id == conversation.id,
                Task.kind == "callback",
                Task.due_at.is_not(None),
            )
        )
    ).all()
    if callback_tasks:
        messages = (
            await session.scalars(
                select(Message)
                .where(Message.conversation_id == conversation.id)
                .order_by(Message.ts.desc(), Message.id.desc())
            )
        ).all()
        source_message = next(
            (message for message in messages if message.role == MessageRole.CALLER),
            messages[0] if messages else None,
        )
        if source_message is None:
            logger.warning(
                "Skipping callback commitment for conversation %s without messages",
                conversation.id,
            )
        else:
            for task in callback_tasks:
                reason = (task.payload or {}).get("reason") or "caller requested a callback"
                what = f"Call back: {reason}"
                exists = await session.scalar(
                    select(Commitment.id).where(
                        Commitment.person_id == person.id,
                        Commitment.source_message_id == source_message.id,
                        Commitment.what == what,
                        Commitment.due_at == task.due_at,
                    )
                )
                if exists is None:
                    session.add(
                        Commitment(
                            tenant_id=tenant_id,
                            person_id=person.id,
                            what=what,
                            due_at=task.due_at,
                            status=CommitmentStatus.OPEN,
                            source_message_id=source_message.id,
                        )
                    )
    await session.flush()


def _format_datetime(value: datetime | None) -> str:
    if value is None:
        return "unscheduled"
    normalized = value.astimezone(UTC) if value.tzinfo else value.replace(tzinfo=UTC)
    return normalized.strftime("%Y-%m-%d %H:%M UTC")


def _compact_value(value: Any, limit: int = 160) -> str:
    rendered = (
        value
        if isinstance(value, str)
        else json.dumps(value, ensure_ascii=False, separators=(",", ":"), default=str)
    )
    if len(rendered) > limit:
        rendered = rendered[: limit - 1] + "…"
    return rendered


async def build_context_card(session: AsyncSession, person_id: UUID) -> str:
    """Build a bounded plain-text summary of the person's recent context."""
    person = await session.scalar(select(Person).where(Person.id == person_id))
    if person is None:
        return ""
    person = await _follow_person(session, person)

    lines = [f"Caller: {person.display_name}"]
    identities = (
        await session.scalars(
            select(Identity)
            .where(Identity.person_id == person.id)
            .order_by(Identity.type, Identity.value_normalized)
        )
    ).all()
    if identities:
        rendered = "; ".join(
            f"{identity.type.value}: {identity.value_normalized}" for identity in identities
        )
        lines.append(f"Known identities: {rendered}")

    conversations = (
        await session.scalars(
            select(Conversation)
            .where(Conversation.person_id == person.id)
            .order_by(Conversation.started_at.desc(), Conversation.id.desc())
            .limit(3)
        )
    ).all()
    if conversations:
        lines.append("Recent conversations:")
        intake_rows = (
            await session.scalars(
                select(Intake).where(
                    Intake.conversation_id.in_([conversation.id for conversation in conversations]),
                    Intake.version == IntakeVersion.MERGED,
                )
            )
        ).all()
        intakes_by_conversation = {intake.conversation_id: intake.data for intake in intake_rows}
        for conversation in conversations:
            summary = (
                conversation.summary
                or _intake_summary(intakes_by_conversation.get(conversation.id))
                or "no summary"
            )
            date = conversation.started_at.date().isoformat()
            lines.append(f"- {date} {conversation.channel.value}: {summary}")

    tasks = (
        await session.scalars(
            select(Task)
            .where(
                Task.person_id == person.id,
                Task.status == TaskStatus.OPEN,
                Task.kind.notin_(("end_call", "knowledge_gap")),
            )
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
    if tasks or commitments:
        lines.append("Open items:")
        for task in tasks:
            reason = (task.payload or {}).get("reason")
            suffix = f": {reason}" if reason else ""
            lines.append(f"- Task {task.kind} due {_format_datetime(task.due_at)}{suffix}")
        for commitment in commitments:
            lines.append(
                f"- Commitment: {commitment.what} (due {_format_datetime(commitment.due_at)})"
            )

    facts = (
        await session.scalars(
            select(Fact)
            .where(
                Fact.subject_type == "person",
                Fact.subject_id == person.id,
                Fact.status == FactStatus.ACTIVE,
                Fact.confidence >= 0.5,
                Fact.predicate != "possible_same_person",
            )
            .order_by(Fact.valid_from.desc(), Fact.recorded_at.desc())
            .limit(20)
        )
    ).all()
    if facts:
        lines.append("Key facts:")
        lines.extend(f"- {fact.predicate}: {_compact_value(fact.value_json)}" for fact in facts)

    result: list[str] = []
    for line in lines:
        candidate = "\n".join((*result, line))
        if len(candidate) > 3200:
            if not result:
                return line[:3200]
            break
        result.append(line)
    return "\n".join(result)


def _id_strings(rows: list[Any]) -> list[str]:
    return [str(row) for row in rows]


async def _person_pair(
    session: AsyncSession, survivor_id: UUID, merged_id: UUID
) -> tuple[Person, Person]:
    survivor = await session.scalar(select(Person).where(Person.id == survivor_id))
    merged = await session.scalar(select(Person).where(Person.id == merged_id))
    if survivor is None or merged is None:
        raise LookupError("Person not found")
    return survivor, merged


async def merge_persons(
    session: AsyncSession,
    *,
    survivor_id: UUID,
    merged_id: UUID,
    actor_user_id: UUID,
) -> UUID:
    """Merge two people and record a reversible audit snapshot."""
    if survivor_id == merged_id:
        raise ValueError("Cannot merge a person into itself")
    survivor, merged = await _person_pair(session, survivor_id, merged_id)
    if survivor.merged_into_id is not None or merged.merged_into_id is not None:
        raise PersonAlreadyMerged("A merge participant is already merged")
    tenant_id = await _tenant_id(session)

    identity_ids = list(
        (
            await session.scalars(
                select(Identity.id).where(Identity.person_id == merged_id).order_by(Identity.id)
            )
        ).all()
    )
    fact_ids = list(
        (
            await session.scalars(
                select(Fact.id)
                .where(Fact.subject_type == "person", Fact.subject_id == merged_id)
                .order_by(Fact.id)
            )
        ).all()
    )
    conversation_ids = list(
        (
            await session.scalars(
                select(Conversation.id)
                .where(Conversation.person_id == merged_id)
                .order_by(Conversation.id)
            )
        ).all()
    )
    task_ids = list(
        (
            await session.scalars(
                select(Task.id).where(Task.person_id == merged_id).order_by(Task.id)
            )
        ).all()
    )
    commitment_ids = list(
        (
            await session.scalars(
                select(Commitment.id)
                .where(Commitment.person_id == merged_id)
                .order_by(Commitment.id)
            )
        ).all()
    )
    suggestions = list(
        (
            await session.scalars(
                select(Fact)
                .where(
                    Fact.predicate == "possible_same_person",
                    Fact.status == FactStatus.ACTIVE,
                    Fact.subject_type == "person",
                    Fact.object_type == "person",
                    or_(
                        and_(
                            Fact.subject_id == survivor_id,
                            Fact.object_id == merged_id,
                        ),
                        and_(
                            Fact.subject_id == merged_id,
                            Fact.object_id == survivor_id,
                        ),
                    ),
                )
                .order_by(Fact.id)
            )
        ).all()
    )
    resolved_suggestion_ids = [fact.id for fact in suggestions]
    if identity_ids:
        await session.execute(
            update(Identity).where(Identity.id.in_(identity_ids)).values(person_id=survivor_id)
        )
    if fact_ids:
        await session.execute(
            update(Fact).where(Fact.id.in_(fact_ids)).values(subject_id=survivor_id)
        )
    if conversation_ids:
        await session.execute(
            update(Conversation)
            .where(Conversation.id.in_(conversation_ids))
            .values(person_id=survivor_id)
        )
    if task_ids:
        await session.execute(
            update(Task).where(Task.id.in_(task_ids)).values(person_id=survivor_id)
        )
    if commitment_ids:
        await session.execute(
            update(Commitment)
            .where(Commitment.id.in_(commitment_ids))
            .values(person_id=survivor_id)
        )
    if resolved_suggestion_ids:
        await session.execute(
            update(Fact)
            .where(Fact.id.in_(resolved_suggestion_ids))
            .values(status=FactStatus.SUPERSEDED, superseded_at=datetime.now(UTC))
        )
    merged.merged_into_id = survivor_id
    log = IdentityMergeLog(
        tenant_id=tenant_id,
        action=IdentityMergeAction.MERGE,
        survivor_id=survivor_id,
        merged_id=merged_id,
        actor_user_id=actor_user_id,
        snapshot={
            "identity_ids": _id_strings(identity_ids),
            "fact_ids": _id_strings(fact_ids),
            "conversation_ids": _id_strings(conversation_ids),
            "task_ids": _id_strings(task_ids),
            "commitment_ids": _id_strings(commitment_ids),
            "resolved_suggestion_fact_ids": _id_strings(resolved_suggestion_ids),
        },
    )
    session.add(log)
    await session.flush()
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action="contact.merge",
            target_type="person",
            target_id=survivor_id,
            diff={"merged_id": str(merged_id), "merge_log_id": str(log.id)},
        )
    )
    await session.flush()
    return survivor_id


async def undo_last_merge(
    session: AsyncSession,
    *,
    person_id: UUID,
    actor_user_id: UUID,
) -> UUID:
    """Undo the latest merge into a person without editing the original log."""
    if await session.scalar(select(Person.id).where(Person.id == person_id)) is None:
        raise LookupError("Person not found")
    merge_logs = (
        await session.scalars(
            select(IdentityMergeLog)
            .where(
                IdentityMergeLog.action == IdentityMergeAction.MERGE,
                IdentityMergeLog.survivor_id == person_id,
            )
            .order_by(IdentityMergeLog.created_at.desc(), IdentityMergeLog.id.desc())
        )
    ).all()
    merge_log = None
    for candidate in merge_logs:
        undone = await session.scalar(
            select(IdentityMergeLog.id).where(
                IdentityMergeLog.action == IdentityMergeAction.SPLIT,
                IdentityMergeLog.snapshot["undo_of"].astext == str(candidate.id),
            )
        )
        if undone is None:
            merge_log = candidate
            break
    if merge_log is None:
        raise LookupError("No merge to undo")
    snapshot = merge_log.snapshot or {}
    merged_id = merge_log.merged_id
    restored_person = await session.scalar(select(Person).where(Person.id == merged_id))
    if restored_person is None:
        raise LookupError("Merged person not found")
    tenant_id = await _tenant_id(session)

    moved_ids: dict[str, list[str]] = {}
    ownership = (
        ("identity_ids", Identity, Identity.person_id),
        ("fact_ids", Fact, Fact.subject_id),
        ("conversation_ids", Conversation, Conversation.person_id),
        ("task_ids", Task, Task.person_id),
        ("commitment_ids", Commitment, Commitment.person_id),
    )
    for key, model, owner_column in ownership:
        ids = [UUID(value) for value in snapshot.get(key, [])]
        if not ids:
            moved_ids[key] = []
            continue
        query = select(model.id).where(model.id.in_(ids), owner_column == person_id)
        if model is Fact:
            query = query.where(Fact.subject_type == "person")
        currently_owned = list((await session.scalars(query)).all())
        moved_ids[key] = _id_strings(currently_owned)
        if currently_owned:
            await session.execute(
                update(model)
                .where(model.id.in_(currently_owned), owner_column == person_id)
                .values({owner_column.key: merged_id})
            )
    suggestion_ids = [UUID(value) for value in snapshot.get("resolved_suggestion_fact_ids", [])]
    restored_suggestions = []
    if suggestion_ids:
        restored_suggestions = list(
            (
                await session.scalars(
                    select(Fact.id).where(
                        Fact.id.in_(suggestion_ids),
                        Fact.predicate == "possible_same_person",
                        Fact.status == FactStatus.SUPERSEDED,
                    )
                )
            ).all()
        )
        if restored_suggestions:
            await session.execute(
                update(Fact)
                .where(Fact.id.in_(restored_suggestions))
                .values(status=FactStatus.ACTIVE, superseded_at=None)
            )
    moved_ids["resolved_suggestion_fact_ids"] = _id_strings(restored_suggestions)
    restored_person.merged_into_id = None

    split_log = IdentityMergeLog(
        tenant_id=tenant_id,
        action=IdentityMergeAction.SPLIT,
        survivor_id=person_id,
        merged_id=merged_id,
        actor_user_id=actor_user_id,
        snapshot={"undo_of": str(merge_log.id), **moved_ids},
    )
    session.add(split_log)
    await session.flush()
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action="contact.merge.undo",
            target_type="person",
            target_id=person_id,
            diff={"undo_of": str(merge_log.id), "restored_person_id": str(merged_id)},
        )
    )
    await session.flush()
    return merged_id


async def split_person(
    session: AsyncSession,
    *,
    person_id: UUID,
    identity_ids: list[UUID],
    actor_user_id: UUID,
) -> UUID:
    """Split identities and their uniquely linked conversation data into a new person."""
    if not identity_ids:
        raise ValueError("At least one identity is required")
    person = await session.scalar(select(Person).where(Person.id == person_id))
    if person is None:
        raise LookupError("Person not found")
    requested_ids = list(dict.fromkeys(identity_ids))
    identities = list(
        (
            await session.scalars(
                select(Identity).where(
                    Identity.id.in_(requested_ids), Identity.person_id == person_id
                )
            )
        ).all()
    )
    if len(identities) != len(requested_ids):
        raise ValueError("Every selected identity must belong to this person")
    identities_by_id = {identity.id: identity for identity in identities}
    identities = [identities_by_id[identity_id] for identity_id in requested_ids]
    tenant_id = await _tenant_id(session)
    split_person = Person(
        tenant_id=tenant_id,
        display_name=identities[0].value_normalized,
    )
    session.add(split_person)
    await session.flush()

    moved_identity_ids = [identity.id for identity in identities]
    retained_identity_ids = list(
        (
            await session.scalars(
                select(Identity.id).where(
                    Identity.person_id == person_id,
                    Identity.id.not_in(moved_identity_ids),
                )
            )
        ).all()
    )
    moved_conversation_ids: list[UUID] = []
    if moved_identity_ids:
        moved_linked = set(
            (
                await session.scalars(
                    select(Edge.src_id).where(
                        Edge.src_type == "conversation",
                        Edge.rel == "via_identity",
                        Edge.dst_type == "identity",
                        Edge.dst_id.in_(moved_identity_ids),
                    )
                )
            ).all()
        )
        retained_linked = set()
        if retained_identity_ids:
            retained_linked = set(
                (
                    await session.scalars(
                        select(Edge.src_id).where(
                            Edge.src_type == "conversation",
                            Edge.rel == "via_identity",
                            Edge.dst_type == "identity",
                            Edge.dst_id.in_(retained_identity_ids),
                        )
                    )
                ).all()
            )
        candidates = moved_linked - retained_linked
        if candidates:
            moved_conversation_ids = list(
                (
                    await session.scalars(
                        select(Conversation.id)
                        .where(
                            Conversation.id.in_(candidates),
                            Conversation.person_id == person_id,
                        )
                        .order_by(Conversation.id)
                    )
                ).all()
            )
    if moved_identity_ids:
        await session.execute(
            update(Identity)
            .where(
                Identity.id.in_(moved_identity_ids),
                Identity.person_id == person_id,
            )
            .values(person_id=split_person.id)
        )
    if moved_conversation_ids:
        await session.execute(
            update(Conversation)
            .where(
                Conversation.id.in_(moved_conversation_ids),
                Conversation.person_id == person_id,
            )
            .values(person_id=split_person.id)
        )
    fact_ids: list[UUID] = []
    task_ids: list[UUID] = []
    commitment_ids: list[UUID] = []
    if moved_conversation_ids:
        fact_ids = list(
            (
                await session.scalars(
                    select(Fact.id)
                    .where(
                        Fact.subject_type == "person",
                        Fact.subject_id == person_id,
                        Fact.source_conversation_id.in_(moved_conversation_ids),
                    )
                    .order_by(Fact.id)
                )
            ).all()
        )
        task_ids = list(
            (
                await session.scalars(
                    select(Task.id)
                    .where(
                        Task.conversation_id.in_(moved_conversation_ids),
                    )
                    .order_by(Task.id)
                )
            ).all()
        )
        message_ids = list(
            (
                await session.scalars(
                    select(Message.id).where(Message.conversation_id.in_(moved_conversation_ids))
                )
            ).all()
        )
        if message_ids:
            commitment_ids = list(
                (
                    await session.scalars(
                        select(Commitment.id)
                        .where(
                            Commitment.source_message_id.in_(message_ids),
                        )
                        .order_by(Commitment.id)
                    )
                ).all()
            )
    if fact_ids:
        await session.execute(
            update(Fact).where(Fact.id.in_(fact_ids)).values(subject_id=split_person.id)
        )
    if task_ids:
        await session.execute(
            update(Task).where(Task.id.in_(task_ids)).values(person_id=split_person.id)
        )
    if commitment_ids:
        await session.execute(
            update(Commitment)
            .where(Commitment.id.in_(commitment_ids))
            .values(person_id=split_person.id)
        )
    snapshot = {
        "identity_ids": _id_strings(moved_identity_ids),
        "fact_ids": _id_strings(fact_ids),
        "conversation_ids": _id_strings(moved_conversation_ids),
        "task_ids": _id_strings(task_ids),
        "commitment_ids": _id_strings(commitment_ids),
        "resolved_suggestion_fact_ids": [],
    }
    session.add(
        IdentityMergeLog(
            tenant_id=tenant_id,
            action=IdentityMergeAction.SPLIT,
            survivor_id=person_id,
            merged_id=split_person.id,
            actor_user_id=actor_user_id,
            snapshot=snapshot,
        )
    )
    await session.flush()
    session.add(
        AuditLog(
            tenant_id=tenant_id,
            actor_user_id=actor_user_id,
            action="contact.split",
            target_type="person",
            target_id=person_id,
            diff={"split_person_id": str(split_person.id), "snapshot": snapshot},
        )
    )
    await session.flush()
    return split_person.id
