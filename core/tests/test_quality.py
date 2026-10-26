import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from inbound_core.languages import DEFAULT_RECORDING_NOTICE, LOCALES
from inbound_core.quality import (
    CHECK_KEYS,
    JUDGE_VERSION,
    MAX_INSTRUCTIONS_CHARS,
    RUBRIC_PROMPT,
    build_judge_prompt,
    judge_conversation,
    needs_review,
    normalize_judgement,
    objective_metrics,
    split_platform_notices,
)


def test_rubric_matches_v3_golden() -> None:
    golden = Path(__file__).with_name("golden") / "quality_rubric_v3.txt"

    assert JUDGE_VERSION == "v3"
    assert RUBRIC_PROMPT == golden.read_text(encoding="utf-8").rstrip("\n")


def test_split_platform_notices_labels_automatic_lines_and_preserves_other_rows() -> None:
    italian_notice = LOCALES["it-IT"].recording_notice
    caller_row = {"role": "caller", "text": f"I heard this notice: {DEFAULT_RECORDING_NOTICE}"}
    unchanged_row = {"role": "assistant", "text": "Hello, how can I help?"}
    rows = [
        {"role": "assistant", "text": DEFAULT_RECORDING_NOTICE},
        {
            "role": "assistant",
            "text": f"Hello, how can I help? {DEFAULT_RECORDING_NOTICE}",
        },
        {
            "role": "assistant",
            "text": f"Before. {italian_notice} Middle. {DEFAULT_RECORDING_NOTICE} After.",
        },
        caller_row,
        unchanged_row,
    ]

    split_rows = split_platform_notices(rows, {DEFAULT_RECORDING_NOTICE, italian_notice})

    assert split_rows == [
        {"role": "platform", "text": DEFAULT_RECORDING_NOTICE},
        {"role": "assistant", "text": "Hello, how can I help?"},
        {"role": "platform", "text": DEFAULT_RECORDING_NOTICE},
        {"role": "assistant", "text": "Before."},
        {"role": "platform", "text": italian_notice},
        {"role": "assistant", "text": "Middle."},
        {"role": "platform", "text": DEFAULT_RECORDING_NOTICE},
        {"role": "assistant", "text": "After."},
        caller_row,
        unchanged_row,
    ]
    assert split_rows[8] is caller_row
    assert split_rows[9] is unchanged_row


def test_normalize_judgement_fills_drops_deduplicates_and_trims() -> None:
    result = normalize_judgement(
        {
            "checks": [
                {"key": "answered_question", "passed": True, "note": " first "},
                {"key": "answered_question", "passed": False, "note": "duplicate"},
                {"key": "unknown", "passed": True, "note": "ignored"},
                {"key": "language_consistent", "passed": "yes", "note": " "},
            ],
            "overall": 8,
            "issues": [" one ", "", "  ", "two", "three", "four", "five", "six"],
        }
    )

    assert result["overall"] == 5
    assert result["checks"] == [
        {"key": "answered_question", "passed": True, "note": "first"},
        {"key": "collected_required_details", "passed": None, "note": None},
        {"key": "language_consistent", "passed": None, "note": None},
        {"key": "no_unsupported_promises", "passed": None, "note": None},
        {"key": "ended_properly", "passed": None, "note": None},
        {"key": "followed_instructions", "passed": None, "note": None},
    ]
    assert [check["key"] for check in result["checks"]] == list(CHECK_KEYS)
    assert result["issues"] == ["one", "two", "three", "four", "five"]


@pytest.mark.parametrize("overall", ["not-a-score", None])
def test_normalize_judgement_rejects_non_integer_overall(overall) -> None:
    with pytest.raises(ValueError):
        normalize_judgement({"overall": overall})


def test_normalize_judgement_clamps_low_score() -> None:
    assert normalize_judgement({"overall": 0})["overall"] == 1


def test_objective_metrics_extracts_voice_metrics_and_skips_text() -> None:
    metrics = {
        "turn_latency_summary": {
            "turn_count": 2,
            "p50_seconds": 0.8,
            "p95_seconds": 1.4,
            "max_seconds": 3.2,
        },
        "turns": [{"seconds": 0.5}, {"seconds": 1.1}],
        "call_duration_seconds": 45.6,
        "usage": {"tts_characters": 120},
        "interruptions": 2,
    }

    assert objective_metrics(metrics) == {
        "response_p50_s": 0.8,
        "response_p95_s": 1.4,
        "max_response_s": 3.2,
        "interruptions": 2,
        "turns": 2,
        "call_duration_s": 45.6,
        "tts_chars": 120,
    }
    assert objective_metrics({}) is None


@pytest.mark.parametrize(
    ("overall", "checks", "objective", "expected"),
    [
        (3, [], None, True),
        (4, [{"passed": False}], None, True),
        (4, [], {"max_response_s": 3.2}, True),
        (4, [], {"max_response_s": 3.0}, False),
        (5, [{"passed": True}], {"max_response_s": 2.9}, False),
    ],
)
def test_needs_review(overall, checks, objective, expected) -> None:
    assert needs_review(overall, checks, objective) is expected


def test_judge_conversation_uses_json_schema_and_normalizes() -> None:
    class FakeModels:
        async def generate_content(self, **kwargs):
            self.arguments = kwargs
            return SimpleNamespace(
                text='{"checks":[{"key":"answered_question","passed":false,"note":" missed "}],'
                '"overall":3,"issues":[" issue "]}'
            )

    models = FakeModels()
    client = SimpleNamespace(aio=SimpleNamespace(models=models))
    result = asyncio.run(judge_conversation(client, "judge-model", "judge prompt"))

    assert (
        models.arguments["config"]["response_json_schema"]["properties"]["overall"]["maximum"] == 5
    )
    assert result["checks"][0] == {
        "key": "answered_question",
        "passed": False,
        "note": "missed",
    }
    assert result["issues"] == ["issue"]


def test_build_judge_prompt_includes_contract_context_and_truncation_marker() -> None:
    prompt = build_judge_prompt(
        instructions="business instructions",
        transcript_rows=[{"role": "caller", "text": "I need help"}],
        intake={"service": "general"},
        tasks=[{"kind": "follow_up", "status": "open"}],
        channel="phone",
    )
    truncated = build_judge_prompt(
        instructions="x" * (MAX_INSTRUCTIONS_CHARS + 1),
        transcript_rows=[],
        intake=None,
        tasks=[],
        channel="text",
    )

    assert prompt.startswith(RUBRIC_PROMPT)
    assert "1. caller: I need help" in prompt
    assert "ASSISTANT INSTRUCTIONS AND POLICIES" in prompt
    assert "TASKS CREATED" in prompt
    assert "[truncated]" in truncated
