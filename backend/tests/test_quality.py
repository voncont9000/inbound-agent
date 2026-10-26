import json
from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import UUID, uuid4

from inbound_core.languages import DEFAULT_RECORDING_NOTICE
from sqlalchemy import select

from inbound_api.db import tenant_session
from inbound_api.models import (
    Assistant,
    Conversation,
    ConversationChannel,
    ConversationScore,
    ConversationStatus,
    Intake,
    IntakeVersion,
    Message,
    MessageRole,
)
from inbound_api.services import quality
from inbound_api.settings import get_settings


def _judgement(overall: int, answered: bool) -> dict:
    return {
        "checks": [
            {"key": "answered_question", "passed": answered, "note": "A concrete issue."},
            {"key": "collected_required_details", "passed": True, "note": None},
        ],
        "overall": overall,
        "issues": ["A concrete issue."] if not answered else [],
    }


def _fake_client(raw: dict, captured: dict) -> SimpleNamespace:
    async def generate_content(**kwargs):
        captured.update(kwargs)
        return SimpleNamespace(text=json.dumps(raw))

    async def close():
        captured["closed"] = True

    models = SimpleNamespace(generate_content=generate_content)
    return SimpleNamespace(aio=SimpleNamespace(models=models, aclose=close))


async def test_conversation_score_upserts_and_sets_review_and_objective(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    tenant_id = UUID(account["tenant_id"])
    assistant_id = uuid4()
    conversation_id = uuid4()
    custom_notice = "This recording is for quality and training."
    captured: list[dict] = []
    responses = [_judgement(5, True), _judgement(4, False)]
    settings = get_settings()
    monkeypatch.setattr(settings, "google_api_key", "test-google-key")
    monkeypatch.setattr(settings, "quality_judge_model", "test-judge-model")

    def create_client(api_key: str) -> SimpleNamespace:
        assert api_key == "test-google-key"
        capture: dict = {}
        captured.append(capture)
        return _fake_client(responses.pop(0), capture)

    monkeypatch.setattr(quality, "make_genai_client", create_client)
    metrics = {
        "turn_latency_summary": {
            "turn_count": 2,
            "p50_seconds": 1.2,
            "p95_seconds": 2.4,
            "max_seconds": 3.0,
        },
        "turns": [{"latency_seconds": 1.2}, {"latency_seconds": 2.4}],
        "call_duration_seconds": 60,
        "usage": {"tts_characters": 99},
        "interruptions": 1,
    }

    async with tenant_session(tenant_id) as session:
        session.add(
            Assistant(
                id=assistant_id,
                tenant_id=tenant_id,
                name="Quality assistant",
                config={
                    "business_name": "Quality assistant",
                    "greeting": "Hello",
                    "office_hours": {
                        "days": ["Monday"],
                        "opens": "09:00",
                        "closes": "17:00",
                    },
                    "price_list": [],
                    "policies": [],
                    "contacts": {},
                    "services": {"general": {"label": "General", "fields": []}},
                    "urgency_rules": [],
                    "live_actions": [],
                    "recording": {"consent_message": custom_notice},
                },
            )
        )
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                channel=ConversationChannel.PHONE,
                external_ref=f"quality-{conversation_id}",
                status=ConversationStatus.COMPLETED,
                started_at=datetime.now(UTC),
                metrics=metrics,
            )
        )
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role=MessageRole.ASSISTANT,
                text=f"Hello, how can I help? {DEFAULT_RECORDING_NOTICE} {custom_notice}",
                ts=datetime.now(UTC),
            )
        )
        session.add(
            Message(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                role=MessageRole.CALLER,
                text="Can you help me?",
                ts=datetime.now(UTC),
            )
        )
        session.add(
            Intake(
                tenant_id=tenant_id,
                conversation_id=conversation_id,
                version=IntakeVersion.MERGED,
                data={"fields": {"email": {"value": "caller@example.test"}}},
            )
        )
        await session.flush()

        first_result = await quality.conversation_score(
            session, {"conversation_id": str(conversation_id)}
        )
        first_score = await session.scalar(
            select(ConversationScore).where(ConversationScore.conversation_id == conversation_id)
        )
        assert first_result == {"overall": 5, "needs_review": False}
        assert first_score is not None
        first_score_id = first_score.id
        assert first_score.judge_model == "test-judge-model"
        assert first_score.judge_version == "v3"
        assert first_score.objective == {
            "response_p50_s": 1.2,
            "response_p95_s": 2.4,
            "max_response_s": 3.0,
            "interruptions": 1,
            "turns": 2,
            "call_duration_s": 60.0,
            "tts_chars": 99,
        }
        first_score.reviewed_at = datetime.now(UTC)
        first_score.reviewed_by = UUID(account["user_id"])
        await session.flush()

        second_result = await quality.conversation_score(
            session, {"conversation_id": str(conversation_id)}
        )
        await session.refresh(first_score)
        rows = list(
            await session.scalars(
                select(ConversationScore).where(
                    ConversationScore.conversation_id == conversation_id
                )
            )
        )
        assert second_result == {"overall": 4, "needs_review": True}
        assert len(rows) == 1
        assert rows[0].id == first_score_id
        assert rows[0].overall == 4
        assert rows[0].needs_review is True
        assert rows[0].reviewed_at is None
        assert rows[0].reviewed_by is None
        assert rows[0].checks[0]["passed"] is False
        assert rows[0].issues == ["A concrete issue."]

    assert captured[0]["config"]["response_json_schema"]["required"] == [
        "checks",
        "overall",
        "issues",
    ]
    assert captured[0]["contents"].startswith("You review one conversation")
    assert f"platform: {DEFAULT_RECORDING_NOTICE}" in captured[0]["contents"]
    assert f"assistant: {DEFAULT_RECORDING_NOTICE}" not in captured[0]["contents"]
    assert f"platform: {custom_notice}" in captured[0]["contents"]
    assert f"assistant: {custom_notice}" not in captured[0]["contents"]
    assert captured[0]["closed"] is True
    assert captured[1]["closed"] is True


async def test_conversation_score_handles_text_and_deleted_conversation(
    tenant_pair, monkeypatch
) -> None:
    account = tenant_pair["first"]
    tenant_id = UUID(account["tenant_id"])
    assistant_id = uuid4()
    conversation_id = uuid4()
    captured: dict = {}
    monkeypatch.setattr(get_settings(), "google_api_key", "test-google-key")
    monkeypatch.setattr(
        quality,
        "make_genai_client",
        lambda _key: _fake_client(_judgement(5, True), captured),
    )

    async with tenant_session(tenant_id) as session:
        session.add(
            Assistant(
                id=assistant_id,
                tenant_id=tenant_id,
                name="Text assistant",
                config={},
            )
        )
        session.add(
            Conversation(
                id=conversation_id,
                tenant_id=tenant_id,
                assistant_id=assistant_id,
                channel=ConversationChannel.WEB,
                external_ref=f"quality-{conversation_id}",
                status=ConversationStatus.COMPLETED,
                started_at=datetime.now(UTC),
                metrics={},
            )
        )
        await session.flush()
        result = await quality.conversation_score(
            session, {"conversation_id": str(conversation_id)}
        )
        score = await session.scalar(
            select(ConversationScore).where(ConversationScore.conversation_id == conversation_id)
        )
        assert result == {"overall": 5, "needs_review": False}
        assert score is not None
        assert score.objective is None
        assert await quality.conversation_score(session, {"conversation_id": str(uuid4())}) is None
