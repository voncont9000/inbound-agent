import json
from datetime import UTC, datetime
from uuid import uuid4

import pytest
from conftest import create_conversation, create_person
from sqlalchemy import func, select

from inbound_api.db import tenant_session
from inbound_api.graph import (
    build_context_card,
    ingest_conversation,
    merge_persons,
    resolve_person,
    split_person,
    undo_last_merge,
)
from inbound_api.graph.normalize import normalize_email, normalize_phone, normalize_whatsapp
from inbound_api.models import (
    AuditLog,
    Commitment,
    Conversation,
    Edge,
    Fact,
    Identity,
    IdentityMergeLog,
    Intake,
    IntakeVersion,
    Person,
    Task,
    TaskSource,
    TaskStatus,
)


def _add_identity(graph_env, person_id, identity_type, value):
    identity_id = uuid4()
    graph_env["execute"](
        "INSERT INTO identities "
        "(id, tenant_id, person_id, type, value_normalized, confidence, source) "
        "VALUES (%s, %s, %s, %s, %s, 1.0, 'test')",
        (identity_id, graph_env["tenant_id"], person_id, identity_type, value),
    )
    return identity_id


def _add_fact(graph_env, person_id, predicate, value, *, confidence=0.9, status="active"):
    fact_id = uuid4()
    graph_env["execute"](
        "INSERT INTO facts "
        "(id, tenant_id, subject_type, subject_id, predicate, value_json, valid_from, "
        "evidence, confidence, status) "
        "VALUES (%s, %s, 'person', %s, %s, %s::jsonb, now(), 'test evidence', %s, %s)",
        (
            fact_id,
            graph_env["tenant_id"],
            person_id,
            predicate,
            json.dumps(value),
            confidence,
            status,
        ),
    )
    return fact_id


def test_normalization():
    assert normalize_phone("07700 900123") == "+447700900123"
    assert normalize_phone("+44 20 7946 0958") == "+442079460958"
    assert normalize_phone("not a phone") is None
    assert normalize_phone("") is None
    assert normalize_email("  Jane.Doe@Example.COM ") == "jane.doe@example.com"
    assert normalize_email("no-address") is None
    assert normalize_whatsapp("whatsapp:+44 7700 900123") == "+447700900123"


@pytest.mark.asyncio
async def test_deterministic_resolution_and_suggestion_without_merge(graph_env):
    phone_person = create_person(graph_env, "Phone match")
    email_person = create_person(graph_env, "Email match")
    _add_identity(graph_env, phone_person, "phone", "+447700900123")
    _add_identity(graph_env, email_person, "email", "jane@example.test")

    async with tenant_session(graph_env["tenant_id"]) as session:
        assert (
            await resolve_person(
                session,
                phone="07700 900123",
                email="jane@example.test",
                source="test",
            )
            == phone_person
        )
        assert (
            await resolve_person(
                session,
                phone=None,
                email="jane@example.test",
                source="test",
            )
            == email_person
        )
        assert (
            await resolve_person(session, phone="+44 20 7946 0958", email=None, source="test")
            is None
        )

    async with tenant_session(graph_env["tenant_id"]) as session:
        suggestion = await session.scalar(
            select(Fact).where(Fact.predicate == "possible_same_person")
        )
        assert suggestion is not None
        assert suggestion.subject_id == phone_person
        assert suggestion.object_id == email_person
        assert suggestion.value_json == {
            "phone": "+447700900123",
            "email": "jane@example.test",
            "source": "test",
        }
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Person)
                .where(Person.id.in_([phone_person, email_person]))
            )
            == 2
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Fact)
                .where(Fact.predicate == "possible_same_person")
            )
            == 1
        )
        assert (
            await session.scalar(
                select(func.count())
                .select_from(Identity)
                .where(Identity.person_id.in_([phone_person, email_person]))
            )
            == 2
        )


@pytest.mark.asyncio
async def test_ingest_without_merged_intake_still_links_caller_id(graph_env):
    conversation_id = create_conversation(
        graph_env,
        metrics={"caller_id": "07700 900130"},
        live={"caller_id": "+44 7700 900130"},
    )
    async with tenant_session(graph_env["tenant_id"]) as session:
        await ingest_conversation(session, conversation_id)
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.person_id is not None
        identity = await session.scalar(
            select(Identity).where(
                Identity.person_id == conversation.person_id,
                Identity.type == "phone",
            )
        )
        assert identity.value_normalized == "+447700900130"
        assert await session.scalar(select(func.count()).select_from(Fact)) == 0


@pytest.mark.asyncio
async def test_ingest_fact_supersession_and_caller_confirmation(graph_env):
    phone = "+447700900124"
    first = create_conversation(
        graph_env,
        metrics={"from_number": phone},
        merged={
            "service": "support",
            "fields": {
                "caller_name": {
                    "value": "Jane Doe",
                    "confidence": 0.95,
                    "status": "filled",
                    "sources": {"post_call": {"evidence": "I am Jane Doe"}},
                }
            },
        },
        started_at="2026-10-17T10:00:00+00:00",
        ended_at="2026-10-17T10:15:00+00:00",
    )
    second = create_conversation(
        graph_env,
        metrics={"from_number": phone},
        merged={
            "service": "support",
            "fields": {
                "caller_name": {
                    "value": "Jane Smith",
                    "confidence": 0.8,
                    "status": "corrected",
                    "sources": {"post_call": {"evidence": "I go by Jane Smith"}},
                }
            },
        },
        started_at="2026-10-17T11:00:00+00:00",
        ended_at="2026-10-17T11:15:00+00:00",
    )
    async with tenant_session(graph_env["tenant_id"]) as session:
        await ingest_conversation(session, first)
        await ingest_conversation(session, second)
        facts = list(
            (
                await session.scalars(
                    select(Fact).where(Fact.predicate == "caller_name").order_by(Fact.valid_from)
                )
            ).all()
        )
        assert [fact.status.value for fact in facts] == ["superseded", "active"]
        assert facts[0].value_json == "Jane Doe"
        assert facts[0].valid_to == datetime(2026, 10, 17, 11, 15, tzinfo=UTC)
        assert facts[0].superseded_at is not None
        assert facts[1].value_json == "Jane Smith"

    confirmed = create_conversation(
        graph_env,
        metrics={"from_number": phone},
        merged={
            "service": "support",
            "fields": {
                "caller_name": {
                    "value": "Jane Confirmed",
                    "confidence": 0.7,
                    "confirmed_by_caller": True,
                    "status": "filled_confirmed",
                }
            },
        },
        started_at="2026-10-17T12:00:00+00:00",
        ended_at="2026-10-17T12:12:00+00:00",
    )
    unconfirmed = create_conversation(
        graph_env,
        metrics={"from_number": phone},
        merged={
            "service": "support",
            "fields": {
                "caller_name": {
                    "value": "Jane Guess",
                    "confidence": 0.4,
                    "status": "filled",
                }
            },
        },
        started_at="2026-10-17T13:00:00+00:00",
        ended_at="2026-10-17T13:12:00+00:00",
    )
    async with tenant_session(graph_env["tenant_id"]) as session:
        await ingest_conversation(session, confirmed)
        await ingest_conversation(session, unconfirmed)
        active = await session.scalar(
            select(Fact).where(
                Fact.predicate == "caller_name",
                Fact.source_conversation_id == confirmed,
            )
        )
        rejected = await session.scalar(
            select(Fact).where(
                Fact.predicate == "caller_name",
                Fact.source_conversation_id == unconfirmed,
            )
        )
        assert active is not None and active.status.value == "active"
        assert rejected is not None and rejected.status.value == "rejected"
        assert rejected.evidence.startswith("Not applied: conflicts with caller-confirmed value. ")


@pytest.mark.asyncio
async def test_ingest_is_idempotent_and_creates_callback_commitment(graph_env):
    conversation_id = create_conversation(
        graph_env,
        merged={
            "service": "support",
            "fields": {
                "phone": {"value": "07700 900125", "confidence": 0.8},
                "email": {"value": " Person@Example.Test ", "confidence": 0.9},
                "caller_name": {"value": "Taylor Caller", "confidence": 0.95},
                "reason": {"value": "reschedule", "confidence": 0.8},
            },
        },
    )
    message_id = uuid4()
    task_id = uuid4()
    due_at = datetime(2026, 10, 20, 10, 0, tzinfo=UTC)
    graph_env["execute"](
        "INSERT INTO messages "
        "(id, tenant_id, conversation_id, role, text, ts) "
        "VALUES (%s, %s, %s, 'caller', 'Please call me back', now())",
        (message_id, graph_env["tenant_id"], conversation_id),
    )
    graph_env["execute"](
        "INSERT INTO tasks "
        "(id, tenant_id, conversation_id, kind, status, due_at, payload, source) "
        "VALUES (%s, %s, %s, 'callback', 'open', %s, %s::jsonb, 'post_call')",
        (
            task_id,
            graph_env["tenant_id"],
            conversation_id,
            due_at,
            json.dumps({"reason": "reschedule"}),
        ),
    )

    async with tenant_session(graph_env["tenant_id"]) as session:
        await ingest_conversation(session, conversation_id)
        models = (Person, Identity, Fact, Commitment, Edge)
        first_counts = [
            await session.scalar(select(func.count()).select_from(model)) for model in models
        ]
        conversation = await session.get(Conversation, conversation_id)
        assert conversation.person_id is not None
        await ingest_conversation(session, conversation_id)
        second_counts = [
            await session.scalar(select(func.count()).select_from(model)) for model in models
        ]
        assert first_counts == second_counts
        assert first_counts[3] == 1
        commitment = await session.scalar(select(Commitment))
        assert commitment.what == "Call back: reschedule"
        assert commitment.kind == "callback"
        assert commitment.source_message_id == message_id
        assert commitment.due_at == due_at
        assert await session.scalar(
            select(Identity).where(Identity.value_normalized == "person@example.test")
        )


@pytest.mark.asyncio
async def test_context_card_and_rls_tenant_isolation(graph_env):
    person_id = create_person(graph_env, "Jordan Example")
    _add_identity(graph_env, person_id, "phone", "+447700900126")
    conversation_id = create_conversation(graph_env, person_id=person_id)
    low_fact = _add_fact(graph_env, person_id, "private_hint", "low confidence", confidence=0.2)
    _add_fact(graph_env, person_id, "customer_name", "Jordan Example")
    message_id = uuid4()
    graph_env["execute"](
        "INSERT INTO messages (id, tenant_id, conversation_id, role, text, ts) "
        "VALUES (%s, %s, %s, 'caller', 'hello', now())",
        (message_id, graph_env["tenant_id"], conversation_id),
    )
    graph_env["execute"](
        "INSERT INTO commitments "
        "(tenant_id, person_id, what, due_at, status, source_message_id) "
        "VALUES (%s, %s, 'Call back: today', '2026-10-20T10:00:00Z', 'open', %s)",
        (graph_env["tenant_id"], person_id, message_id),
    )
    foreign_tenant_id, foreign_person_id = uuid4(), uuid4()
    graph_env["execute"](
        "INSERT INTO tenants (id, name) VALUES (%s, 'Foreign')",
        (foreign_tenant_id,),
    )
    graph_env["execute"](
        "INSERT INTO persons (id, tenant_id, display_name) VALUES (%s, %s, 'Other Person')",
        (foreign_person_id, foreign_tenant_id),
    )
    try:
        async with tenant_session(graph_env["tenant_id"]) as session:
            conversation = await session.get(Conversation, conversation_id)
            conversation.summary = None
            session.add(
                Intake(
                    tenant_id=graph_env["tenant_id"],
                    conversation_id=conversation_id,
                    version=IntakeVersion.MERGED,
                    data={
                        "service": "account support",
                        "fields": {
                            "customer_name": {"value": "Jordan Example", "confidence": 0.9},
                            "private_hint": {"value": "hidden", "confidence": 0.2},
                        },
                    },
                )
            )
            for kind in ("end_call", "knowledge_gap"):
                session.add(
                    Task(
                        tenant_id=graph_env["tenant_id"],
                        person_id=person_id,
                        conversation_id=conversation_id,
                        kind=kind,
                        status=TaskStatus.OPEN,
                        payload={},
                        source=TaskSource.LIVE,
                    )
                )
        async with tenant_session(graph_env["tenant_id"]) as session:
            card = await build_context_card(session, person_id)
            assert "Jordan Example" in card
            assert "phone: +447700900126" in card
            assert "account support; customer_name: Jordan Example" in card
            assert "Call back: today" in card
            assert "customer_name: Jordan Example" in card
            assert "private_hint" not in card
            assert "end_call" not in card
            assert "knowledge_gap" not in card
            assert len(card) <= 3200
            assert await build_context_card(session, foreign_person_id) == ""
        async with tenant_session(foreign_tenant_id) as session:
            foreign_card = await build_context_card(session, foreign_person_id)
            assert "Other Person" in foreign_card
            assert "Jordan Example" not in foreign_card
            assert low_fact is not None
    finally:
        graph_env["execute"]("DELETE FROM tenants WHERE id = %s", (foreign_tenant_id,))


@pytest.mark.asyncio
async def test_merge_undo_restores_snapshot_and_writes_audit(graph_env):
    survivor_id = create_person(graph_env, "Survivor")
    merged_id = create_person(graph_env, "Merged")
    identity_id = _add_identity(graph_env, merged_id, "email", "merged@example.test")
    conversation_id = create_conversation(graph_env, person_id=merged_id)
    fact_id = _add_fact(graph_env, merged_id, "topic", "billing")
    message_id = uuid4()
    task_id = uuid4()
    graph_env["execute"](
        "INSERT INTO messages (id, tenant_id, conversation_id, role, text, ts) "
        "VALUES (%s, %s, %s, 'caller', 'hello', now())",
        (message_id, graph_env["tenant_id"], conversation_id),
    )
    graph_env["execute"](
        "INSERT INTO tasks (id, tenant_id, conversation_id, person_id, kind, source) "
        "VALUES (%s, %s, %s, %s, 'callback', 'manual')",
        (task_id, graph_env["tenant_id"], conversation_id, merged_id),
    )
    commitment_id = uuid4()
    graph_env["execute"](
        "INSERT INTO commitments "
        "(id, tenant_id, person_id, what, source_message_id) "
        "VALUES (%s, %s, %s, 'Call back', %s)",
        (commitment_id, graph_env["tenant_id"], merged_id, message_id),
    )
    async with tenant_session(graph_env["tenant_id"]) as session:
        before = (
            await session.scalar(select(Identity.person_id).where(Identity.id == identity_id)),
            await session.scalar(select(Fact.subject_id).where(Fact.id == fact_id)),
            await session.scalar(
                select(Conversation.person_id).where(Conversation.id == conversation_id)
            ),
            await session.scalar(select(Task.person_id).where(Task.id == task_id)),
            await session.scalar(
                select(Commitment.person_id).where(Commitment.id == commitment_id)
            ),
            await session.scalar(select(Person.merged_into_id).where(Person.id == merged_id)),
        )
        assert before == (merged_id, merged_id, merged_id, merged_id, merged_id, None)
        assert (
            await merge_persons(
                session,
                survivor_id=survivor_id,
                merged_id=merged_id,
                actor_user_id=graph_env["user_id"],
            )
            == survivor_id
        )
        assert (
            await session.scalar(select(Identity.person_id).where(Identity.id == identity_id))
            == survivor_id
        )
        assert (
            await undo_last_merge(
                session,
                person_id=survivor_id,
                actor_user_id=graph_env["user_id"],
            )
            == merged_id
        )
        after = (
            await session.scalar(select(Identity.person_id).where(Identity.id == identity_id)),
            await session.scalar(select(Fact.subject_id).where(Fact.id == fact_id)),
            await session.scalar(
                select(Conversation.person_id).where(Conversation.id == conversation_id)
            ),
            await session.scalar(select(Task.person_id).where(Task.id == task_id)),
            await session.scalar(
                select(Commitment.person_id).where(Commitment.id == commitment_id)
            ),
            await session.scalar(select(Person.merged_into_id).where(Person.id == merged_id)),
        )
        assert after == before
        assert await session.scalar(select(func.count()).select_from(IdentityMergeLog)) == 2
        assert {
            row[0]
            for row in (
                await session.execute(
                    select(AuditLog.action).where(AuditLog.target_id == survivor_id)
                )
            ).all()
        } == {"contact.merge", "contact.merge.undo"}


@pytest.mark.asyncio
async def test_split_reassigns_conversation_facts_for_moved_identity(graph_env):
    person_id = create_person(graph_env, "Combined Person")
    merged_person_id = create_person(graph_env, "Second Person")
    moved_identity_id = _add_identity(graph_env, merged_person_id, "phone", "+447700900127")
    second_identity_id = _add_identity(graph_env, merged_person_id, "email", "second@example.test")
    identity_values = {
        moved_identity_id: "+447700900127",
        second_identity_id: "second@example.test",
    }
    identity_ids = sorted(identity_values, key=str, reverse=True)
    conversation_id = create_conversation(graph_env, person_id=merged_person_id)
    fact_id = _add_fact(graph_env, merged_person_id, "issue", "callback", confidence=0.8)
    graph_env["execute"](
        "UPDATE facts SET source_conversation_id = %s WHERE id = %s",
        (conversation_id, fact_id),
    )
    task_id = uuid4()
    message_id = uuid4()
    commitment_id = uuid4()
    graph_env["execute"](
        "INSERT INTO tasks (id, tenant_id, conversation_id, kind, status, payload, source) "
        "VALUES (%s, %s, %s, 'callback', 'open', '{}'::jsonb, 'post_call')",
        (task_id, graph_env["tenant_id"], conversation_id),
    )
    graph_env["execute"](
        "INSERT INTO messages (id, tenant_id, conversation_id, role, text, ts) "
        "VALUES (%s, %s, %s, 'caller', 'Call back', now())",
        (message_id, graph_env["tenant_id"], conversation_id),
    )
    graph_env["execute"](
        "INSERT INTO commitments "
        "(id, tenant_id, person_id, what, status, source_message_id) "
        "VALUES (%s, %s, %s, 'Call back', 'open', %s)",
        (commitment_id, graph_env["tenant_id"], merged_person_id, message_id),
    )
    graph_env["execute"](
        "INSERT INTO edges "
        "(tenant_id, src_type, src_id, rel, dst_type, dst_id, valid_from) "
        "VALUES (%s, 'conversation', %s, 'via_identity', 'identity', %s, now())",
        (graph_env["tenant_id"], conversation_id, moved_identity_id),
    )
    async with tenant_session(graph_env["tenant_id"]) as session:
        await merge_persons(
            session,
            survivor_id=person_id,
            merged_id=merged_person_id,
            actor_user_id=graph_env["user_id"],
        )
        split_id = await split_person(
            session,
            person_id=person_id,
            identity_ids=identity_ids,
            actor_user_id=graph_env["user_id"],
        )
        assert (
            await session.scalar(select(Identity.person_id).where(Identity.id == moved_identity_id))
            == split_id
        )
        assert (
            await session.scalar(
                select(Conversation.person_id).where(Conversation.id == conversation_id)
            )
            == split_id
        )
        assert await session.scalar(select(Fact.subject_id).where(Fact.id == fact_id)) == split_id
        assert await session.scalar(select(Task.person_id).where(Task.id == task_id)) == split_id
        assert (
            await session.scalar(select(Commitment.person_id).where(Commitment.id == commitment_id))
            == split_id
        )
        assert (
            await session.scalar(select(Person.display_name).where(Person.id == split_id))
            == identity_values[identity_ids[0]]
        )
