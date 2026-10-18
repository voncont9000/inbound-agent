import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from types import SimpleNamespace

from inbound_voice.config import load_assistant_config
from inbound_voice.extraction import (
    _extraction_schema,
    build_intake_artifacts,
    build_post_call_tasks,
    extract_intake,
    merge_intake,
    reextract_run,
)
from inbound_voice.intake import build_service_model
from inbound_voice.tools import DEFAULT_CONFIG_PATH


class FakeModels:
    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.arguments = None

    async def generate_content(self, **arguments):
        self.arguments = arguments
        if self.error:
            raise self.error
        return self.response


class FakeClient:
    def __init__(self, response=None, error=None):
        self.models = FakeModels(response=response, error=error)
        self.aio = SimpleNamespace(models=self.models)


def _response(fields, unanswered_questions=None):
    return SimpleNamespace(
        text=json.dumps(
            {
                "service": "employment",
                "fields": fields,
                "callback_requested": {
                    "requested": True,
                    "reason": "Discuss my case",
                    "preferred_time": "tomorrow morning",
                    "preferred_time_iso": "2026-02-03T10:00:00+00:00",
                },
                "summary": "The caller asked about an employment dismissal.",
                "complaint": {"made": True, "summary": "Delay returning a call"},
                "unanswered_questions": unanswered_questions or [],
            }
        ),
        usage_metadata=SimpleNamespace(prompt_token_count=100, candidates_token_count=25),
    )


def test_extraction_coerces_values_and_marks_invalid_values() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    client = FakeClient(
        _response(
            [
                {
                    "name": "caller_name",
                    "value": "  Alex Smith ",
                    "confidence": 0.91,
                    "evidence": "My name is Alex Smith.",
                },
                {
                    "name": "email",
                    "value": "not-an-email",
                    "confidence": 0.8,
                    "evidence": "My email is not-an-email.",
                },
            ],
            unanswered_questions=[
                {
                    "question": "Do you discount for NHS workers?",
                    "evidence": "Do you discount for NHS workers?",
                }
            ],
        )
    )

    result = asyncio.run(
        extract_intake(
            [{"role": "caller", "text": "My name is Alex Smith."}],
            {"service": "employment", "fields": {}},
            config,
            client,
            "gemini-test",
        )
    )

    assert result["status"] == "completed"
    assert result["summary"] == "The caller asked about an employment dismissal."
    assert result["fields"]["caller_name"]["value"] == "  Alex Smith "
    assert result["fields"]["email"] == {
        "value": "not-an-email",
        "confidence": 0.0,
        "evidence": "My email is not-an-email.",
        "confirmed_by_caller": False,
        "invalid": True,
    }
    assert result["fields"]["caller_name"]["confirmed_by_caller"] is False
    assert result["callback_requested"] == {
        "requested": True,
        "reason": "Discuss my case",
        "preferred_time": "tomorrow morning",
        "preferred_time_iso": "2026-02-03T10:00:00+00:00",
    }
    assert result["complaint"] == {"made": True, "summary": "Delay returning a call"}
    assert result["unanswered_questions"] == [
        {
            "question": "Do you discount for NHS workers?",
            "evidence": "Do you discount for NHS workers?",
        }
    ]
    assert result["usage"] == {"input_tokens": 100, "output_tokens": 25}
    generation_config = client.models.arguments["config"]
    assert generation_config["response_mime_type"] == "application/json"
    assert "response_schema" not in generation_config
    assert "response_json_schema" in generation_config
    schema = generation_config["response_json_schema"]
    assert schema["properties"]["service"]["enum"] == list(config.services)
    assert schema["properties"]["fields"]["items"]["properties"]["name"]["enum"] == sorted(
        {field.name for service in config.services.values() for field in service.fields}
    )
    item_properties = schema["properties"]["fields"]["items"]["properties"]
    assert {"type": "array", "items": {"type": "string"}} in item_properties["value"]["anyOf"]
    assert item_properties["confirmed_by_caller"]["type"] == "boolean"
    assert "callback_requested" in schema["properties"]
    assert "complaint" in schema["properties"]
    assert "unanswered_questions" in schema["required"]
    assert "unanswered_questions" in schema["properties"]
    assert (
        "List in unanswered_questions each question the caller asked that the assistant did not "
        "answer from business knowledge" in client.models.arguments["contents"]
    )
    assert (
        "Giving a name, phone number or email is not agreement to a callback. "
        "callback_requested is true only if the caller asks for a callback or answers yes to an "
        "offer of one. An assistant offer without caller agreement is false."
        in client.models.arguments["contents"]
    )
    assert (
        "Resolve a clear preferred time into preferred_time_iso"
        in client.models.arguments["contents"]
    )
    assert '"2026-08-10: dismissed"' in client.models.arguments["contents"]
    assert schema == _extraction_schema(config)
    assert "1. caller:" in client.models.arguments["contents"]
    assert (
        "When the caller spells a value, preserve that spelling."
        in client.models.arguments["contents"]
    )
    assert '"siobhan.mcallister@" means "Siobhan McAllister"' in client.models.arguments["contents"]


def test_extraction_can_be_skipped_or_fail_without_raising() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    skipped = asyncio.run(extract_intake([], {}, config, None, "gemini-test"))
    failed = asyncio.run(
        extract_intake([], {}, config, FakeClient(error=RuntimeError("offline")), "x")
    )

    assert skipped["status"] == "skipped"
    assert skipped["callback_requested"]["requested"] is False
    assert failed["status"] == "failed"
    assert "offline" in failed["error"]


def test_caller_id_populates_phone_with_provenance() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    result = {
        "service": "employment",
        "status": "completed",
        "model": "test",
        "fields": {},
        "callback_requested": {},
        "complaint": {},
    }

    post_call, merged = build_intake_artifacts(
        {"service": "employment", "fields": {}},
        result,
        config,
        caller_id="07700900456",
    )

    assert post_call["fields"]["phone"] == {
        "value": "+447700900456",
        "confidence": 1.0,
        "evidence": "Caller ID",
        "confirmed_by_caller": False,
        "source": "caller_id",
    }
    assert merged["fields"]["phone"]["status"] == "caller_id"
    assert merged["fields"]["phone"]["value"] == "+447700900456"
    assert merged["fields"]["phone"]["confidence"] == 1.0


def test_stated_phone_overrides_caller_id_and_keeps_alternative() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    result = {
        "service": "employment",
        "status": "completed",
        "model": "test",
        "fields": {
            "phone": {
                "value": "07700111222",
                "confidence": 0.9,
                "evidence": "My other number is 07700 111222.",
                "confirmed_by_caller": False,
            }
        },
        "callback_requested": {},
        "complaint": {},
    }

    post_call, merged = build_intake_artifacts(
        {"service": "employment", "fields": {}},
        result,
        config,
        caller_id="07700900456",
    )

    assert post_call["fields"]["phone"]["value"] == "07700111222"
    assert post_call["fields"]["phone"]["alternatives"] == ["+447700900456"]
    assert merged["fields"]["phone"]["value"] == "07700111222"
    assert merged["fields"]["phone"]["alternatives"] == ["+447700900456"]
    assert merged["fields"]["phone"]["sources"]["caller_id"]["value"] == "+447700900456"


def test_reextract_run_rewrites_intake_and_updates_extraction_cost(tmp_path, monkeypatch) -> None:
    import inbound_voice.extraction as extraction

    (tmp_path / "transcript.jsonl").write_text(
        json.dumps({"role": "caller", "text": "I was dismissed."}) + "\n",
        encoding="utf-8",
    )
    (tmp_path / "intake.live.json").write_text(
        json.dumps({"service": "employment", "fields": {}}),
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(
        json.dumps(
            {
                "transport": "webrtc",
                "call_duration_seconds": 60,
                "providers": {
                    "stt": "soniox",
                    "tts": "speechify",
                    "llm_model": "gemini-3.5-flash-lite",
                },
                "usage": {
                    "tts_characters": 0,
                    "llm_input_tokens": 0,
                    "llm_output_tokens": 0,
                },
            }
        ),
        encoding="utf-8",
    )

    async def fake_extract_intake(transcript_rows, live_intake, config, client, model, **_kwargs):
        assert transcript_rows[0]["text"] == "I was dismissed."
        assert live_intake["service"] == "employment"
        assert client is None
        return {
            "status": "completed",
            "service": "employment",
            "model": model,
            "fields": {
                "issue_type": {
                    "value": "dismissal",
                    "confidence": 0.9,
                    "evidence": "I was dismissed.",
                }
            },
            "unanswered_questions": [
                {
                    "question": "Can you discount for NHS staff?",
                    "evidence": "Do you offer a discount for NHS staff?",
                }
            ],
            "usage": {"input_tokens": 1_000_000, "output_tokens": 1_000_000},
        }

    monkeypatch.setattr(extraction, "extract_intake", fake_extract_intake)
    monkeypatch.setenv("EXTRACTION_MODEL", "gemini-3.8-flash")
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    result = asyncio.run(reextract_run(tmp_path, DEFAULT_CONFIG_PATH))

    merged = json.loads((tmp_path / "intake.json").read_text(encoding="utf-8"))
    post_call = json.loads((tmp_path / "intake.post_call.json").read_text(encoding="utf-8"))
    metrics = json.loads((tmp_path / "metrics.json").read_text(encoding="utf-8"))
    gaps = json.loads((tmp_path / "knowledge_gaps.json").read_text(encoding="utf-8"))
    tasks = json.loads((tmp_path / "tasks.json").read_text(encoding="utf-8"))
    assert result["extraction_status"] == "completed"
    assert merged["fields"]["issue_type"]["status"] == "filled"
    assert post_call["fields"]["issue_type"]["value"] == "dismissal"
    assert metrics["usage"]["extraction_input_tokens"] == 1_000_000
    assert metrics["estimated_cost"]["components"]["extraction"] == 4.5
    assert gaps["unanswered_questions"][0]["evidence"] == "Do you offer a discount for NHS staff?"
    assert any(task["type"] == "knowledge_gap" and task["source"] == "post_call" for task in tasks)


def test_merge_intake_covers_agreed_live_filled_conflict_and_corrected() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])
    live = {
        "service": "employment",
        "fields": {
            "caller_name": {"value": "Alex Smith", "confirmed": True, "confirmed_at": "now"},
            "phone": {"value": "+44 7700 900123", "confirmed": True, "confirmed_at": "now"},
            "email": {"value": "old@example.com", "confirmed": True, "confirmed_at": "now"},
            "employer_name": {"value": "Old Ltd", "confirmed": False, "confirmed_at": "now"},
        },
    }
    post = {
        "service": "employment",
        "fields": {
            "caller_name": {
                "value": "alex smith",
                "confidence": 0.7,
                "evidence": "I'm Alex Smith.",
            },
            "phone": {"value": None, "confidence": 0.1, "evidence": ""},
            "email": {
                "value": "new@example.com",
                "confidence": 0.9,
                "evidence": "Use new@example.com.",
            },
            "employer_name": {
                "value": "New Ltd",
                "confidence": 0.8,
                "evidence": "I work for New Ltd.",
            },
            "issue_type": {
                "value": "dismissal",
                "confidence": 0.8,
                "evidence": "I was dismissed.",
            },
        },
    }

    result = merge_intake(live, post, service_model, 0.7)

    assert result["fields"]["caller_name"]["status"] == "agreed"
    assert result["fields"]["phone"]["status"] == "live_only"
    assert result["fields"]["email"]["status"] == "conflict"
    assert result["fields"]["email"]["alternative"] == "new@example.com"
    assert result["fields"]["employer_name"]["status"] == "corrected"
    assert result["fields"]["employer_name"]["alternative"] == "Old Ltd"
    assert result["fields"]["issue_type"]["status"] == "filled"
    assert result["needs_review"] == ["email"]


def test_merge_intake_flags_low_confidence_and_invalid_fields() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])
    post = {
        "service": "employment",
        "fields": {
            "caller_name": {
                "value": "Alex",
                "confidence": 0.2,
                "evidence": "I'm Alex.",
            },
            "email": {
                "value": "invalid-email",
                "confidence": 0,
                "evidence": "my email is invalid-email",
                "invalid": True,
            },
        },
    }

    result = merge_intake({"service": None, "fields": {}}, post, service_model, 0.7)

    assert result["needs_review"] == ["caller_name", "email"]
    assert result["fields"]["email"]["invalid"] is True


def test_post_call_only_confirmed_field_gets_confirmed_status() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])

    result = merge_intake(
        {"service": None, "fields": {}},
        {
            "service": "employment",
            "fields": {
                "caller_name": {
                    "value": "Alex Smith",
                    "confidence": 0.9,
                    "evidence": "My name is Alex Smith.",
                    "confirmed_by_caller": True,
                }
            },
        },
        service_model,
        0.7,
    )

    assert result["fields"]["caller_name"]["status"] == "filled_confirmed"
    assert result["fields"]["caller_name"]["confirmed_by_caller"] is True


def test_post_call_tasks_use_merged_deadlines_and_sources() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    call_ended_at = datetime(2026, 2, 2, 12, tzinfo=UTC)
    deadline_date = (date.today() - timedelta(days=70)).isoformat()
    post_call = {
        "callback_requested": {"requested": True, "reason": "Discuss options"},
        "complaint": {"made": True, "summary": "No response"},
    }
    merged = {
        "service": "employment",
        "fields": {"termination_date": {"value": deadline_date}},
    }

    tasks = build_post_call_tasks(post_call, merged, config, call_ended_at=call_ended_at)

    assert [task["type"] for task in tasks] == ["urgent", "callback", "complaint"]
    assert all(task["source"] == "post_call" for task in tasks)
    assert tasks[0]["value"] == deadline_date
    assert build_post_call_tasks(
        post_call,
        merged,
        config,
        [{"type": "urgent", "rule": 1, "value": deadline_date, "source": "live"}],
        call_ended_at=call_ended_at,
    ) == [
        {
            "type": "callback",
            "reason": "Discuss options",
            "preferred_time": None,
            "due_at": "2026-02-03T09:00:00+00:00",
            "source": "post_call",
        },
        {"type": "complaint", "summary": "No response", "source": "post_call"},
    ]


def test_callback_due_at_respects_preference_hours_and_fourteen_day_window() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    ended_at = datetime(2026, 2, 2, 12, tzinfo=UTC)
    merged = {"service": "employment", "fields": {}}

    def due(preferred_time_iso: str | None, *, office_hours=True):
        assistant = config
        if not office_hours:
            assistant = config.model_copy(
                update={"office_hours": config.office_hours.model_copy(update={"days": []})}
            )
        tasks = build_post_call_tasks(
            {
                "callback_requested": {
                    "requested": True,
                    "reason": "Call me",
                    "preferred_time": "tomorrow morning",
                    "preferred_time_iso": preferred_time_iso,
                }
            },
            merged,
            assistant,
            call_ended_at=ended_at,
        )
        return next(task for task in tasks if task["type"] == "callback")

    assert due("2026-02-02T15:00:00+00:00")["due_at"] == "2026-02-02T15:00:00+00:00"
    assert due("2026-02-02T11:00:00+00:00")["due_at"] == "2026-02-03T09:00:00+00:00"
    assert due("2026-02-02T18:00:00+00:00")["due_at"] == "2026-02-03T09:00:00+00:00"
    assert due("not-a-date")["due_at"] == "2026-02-03T09:00:00+00:00"
    assert due("2026-03-01T10:00:00+00:00")["due_at"] == "2026-02-03T09:00:00+00:00"
    assert due("2026-02-02T15:00:00+00:00", office_hours=False)["due_at"] == (
        "2026-02-02T15:00:00+00:00"
    )
    assert due("invalid", office_hours=False)["due_at"] is None


def test_key_dates_is_a_list_of_strings() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])

    validated = service_model.model_validate(
        {"key_dates": ["2026-08-10: dismissed", "2026-08-12: appeal"]}
    )

    assert validated.key_dates == ["2026-08-10: dismissed", "2026-08-12: appeal"]


def test_merge_intake_normalizes_phone_numbers() -> None:
    config = load_assistant_config(DEFAULT_CONFIG_PATH)
    service_model = build_service_model(config.services["employment"])

    result = merge_intake(
        {
            "service": "employment",
            "fields": {
                "phone": {
                    "value": "+44 7700 900123",
                    "confirmed": True,
                    "confirmed_at": "now",
                }
            },
        },
        {
            "service": "employment",
            "fields": {
                "phone": {
                    "value": "+44 (7700) 900123",
                    "confidence": 0.8,
                    "evidence": "07700 900123",
                }
            },
        },
        service_model,
        0.7,
    )

    assert result["fields"]["phone"]["status"] == "agreed"
