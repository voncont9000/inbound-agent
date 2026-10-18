import json
import shutil
import time
from pathlib import Path

import pytest

from eval import twilio_sim
from eval.score import aggregate_calls, render_summary, score_call
from eval.synth_callers import load_scenarios
from inbound_voice.config import load_assistant_config
from inbound_voice.twilio import compute_signature


def test_latency_breakdown_aligns_turns_with_service_metrics() -> None:
    from eval.score import _latency_breakdown_medians

    result = _latency_breakdown_medians(
        [
            {
                "vad_user_stopped_speaking_at": 1.5,
                "user_stopped_speaking_at": 2.0,
                "first_bot_audio_at": 3.0,
            }
        ],
        [
            {
                "processor": "LLMService",
                "kind": "ttfb",
                "model": "gemini-test",
                "monotonic_timestamp": 2.2,
            },
            {
                "processor": "TTSService",
                "kind": "ttfa",
                "model": "speechify-test",
                "monotonic_timestamp": 2.7,
            },
        ],
    )

    assert result["caller_stop_to_turn_end_seconds"] == 0.5
    assert result["turn_end_to_llm_first_token_seconds"] == pytest.approx(0.2)
    assert result["llm_first_token_to_tts_first_audio_seconds"] == pytest.approx(0.5)


FIXTURE = Path(__file__).parent / "fixtures" / "eval-call"
SCENARIO = {
    "transcript_checks": {
        "caller": "mc ?allister",
        "employer": "brightwell",
        "acas": r"\bacas\b",
    },
    "intake_checks": {
        "caller_name": "(?i)siobhan.*mc ?allister",
        "termination_date": "^2026-08-10$",
        "acas_early_conciliation_started": "^False$",
        "missing": "anything",
        "null_field": "None",
    },
    "task_checks": {"urgent": True},
}


def test_score_call_uses_fixture_transcript_intake_tasks_and_metrics(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["providers"]["tts_voice"] = "voice-123"
    metrics["providers"]["llm"] = "openrouter"
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    result = score_call(run_dir, SCENARIO)

    assert result["call_id"] == "fixture-call"
    assert result["transcript_checks"]["caller"]["passed"]
    assert result["transcript_checks"]["acas"]["passed"]
    assert result["intake_checks"]["termination_date"]["passed"]
    assert result["intake_checks"]["acas_early_conciliation_started"]["value"] is False
    assert result["intake_checks"]["acas_early_conciliation_started"]["passed"]
    assert not result["intake_checks"]["missing"]["passed"]
    assert result["task_checks"]["urgent"]["passed"]
    assert not result["intake_checks"]["null_field"]["passed"]
    assert result["checks_passed"] == 7
    assert result["checks_total"] == 9
    assert result["stt_ttfb_median_seconds"] == 0.2
    assert result["tts_ttfb_median_seconds"] == 0.15
    assert result["llm_ttfb_median_seconds"] is None
    assert result["ended_via_end_call"]
    assert result["tts_voice"] == "voice-123"
    assert result["llm"] == "openrouter"
    assert result["llm_model"] == "google/gemini-2.5-flash"
    assert result["estimated_cost"]["components"]["stt"] == pytest.approx(0.015)
    assert result["live_intake_checks"]["caller_name"]["passed"]
    assert result["post_call_intake_checks"]["caller_name"]["passed"]
    assert result["needs_review"] == []


def test_score_call_checks_end_source_and_disconnect_timing(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "tasks.json").write_text(
        json.dumps([{"type": "end_call", "source": "idle"}]), encoding="utf-8"
    )
    (run_dir / "caller_timeline.json").write_text(
        json.dumps(
            {
                "server_disconnect_detected": True,
                "seconds_from_last_agent_speech_end_to_disconnect": 1.5,
                "number_holds": [
                    {
                        "number_hold_started": 4.0,
                        "resumed": True,
                        "held_secs": 0.8,
                    }
                ],
            }
        ),
        encoding="utf-8",
    )

    result = score_call(
        run_dir,
        {
            "task_checks": {"callback": False},
            "expected_end_call_source": "idle",
            "expected_server_disconnect": True,
        },
    )

    assert result["task_checks"]["end_call_source"]["passed"]
    assert result["task_checks"]["server_disconnect_detected"]["passed"]
    assert result["task_checks"]["callback"]["passed"]
    assert result["end_call_source"] == "idle"
    assert result["server_disconnect_detected"]
    assert result["seconds_from_last_agent_speech_end_to_disconnect"] == 1.5
    assert result["number_holds"][0]["resumed"]
    assert result["number_hold_resumed_count"] == 1
    assert result["number_hold_not_resumed_count"] == 0

    (run_dir / "tasks.json").write_text(
        json.dumps([{"type": "callback"}]),
        encoding="utf-8",
    )
    callback_found = score_call(
        run_dir,
        {"task_checks": {"callback": False}},
    )
    assert not callback_found["task_checks"]["callback"]["passed"]


def test_scenarios_script_final_confirmation_and_named_idle_check() -> None:
    scenarios = load_scenarios()
    final_confirmation = "Yes, that's all correct. Thanks, bye."

    assert scenarios["employment"]["utterances"][-1] == final_confirmation
    assert scenarios["conveyancing"]["utterances"][-1] == final_confirmation
    assert scenarios["idle_check"]["utterances"] == ["Hi, I'm calling about a property purchase."]
    assert scenarios["idle_check"]["expected_end_call_source"] == "idle"
    assert scenarios["idle_check"]["expected_server_disconnect"] is True
    assert len(scenarios["knowledge_check"]["utterances"]) == 7
    knowledge_check = scenarios["knowledge_check"]
    assert "| pause 1.4 |" in knowledge_check["utterances"][4]
    assert "phone_readback" not in knowledge_check["assistant_checks"]
    assert knowledge_check["task_checks"]["callback"] is False
    assert knowledge_check["task_checks"]["number_hold_resumed"] is True
    assert knowledge_check["no_agent_speech_between_segments"] is True
    assert knowledge_check["assistant_max_matches"]["callback_offers"] == {
        "pattern": "(?i)call ?back",
        "max": 1,
    }
    assert scenarios["employment"]["caller_id"] == "07700900456"
    assert scenarios["employment"]["utterances"][2] == "Yes, that's fine."
    employment_utterances = " ".join(scenarios["employment"]["utterances"]).casefold()
    assert "my mobile" not in employment_utterances
    assert "07700900123" not in employment_utterances
    assert "email_readback" in scenarios["employment"]["assistant_checks"]
    assert scenarios["conveyancing"]["assistant_not_matches"] == "(?i)call ?back"
    assert "knowledge_check" not in {"employment", "conveyancing", "idle_check"}
    assert scenarios["booking"]["assistant"] == "config/assistants/booking_firm.yaml"
    assert scenarios["booking"]["booking_checks"] == {
        "booked": True,
        "appointment_type": "initial_consultation",
    }


def test_booking_checks_are_scored_from_fake_api_state(tmp_path) -> None:
    run_dir = tmp_path / "booking-score"
    shutil.copytree(FIXTURE, run_dir)
    scenario = {"booking_checks": {"booked": True, "appointment_type": "initial_consultation"}}

    score = score_call(
        run_dir,
        scenario,
        fake_state={
            "bookings": [
                {
                    "appointment_type": "initial_consultation",
                    "booking": {"status": "booked"},
                }
            ]
        },
    )

    assert all(item["passed"] for item in score["booking_checks"].values())


def test_booking_scenario_assistant_fixture_enables_booking() -> None:
    scenario = load_scenarios()["booking"]
    assistant_path = Path(__file__).resolve().parents[1] / scenario["assistant"]
    assistant = load_assistant_config(assistant_path)

    assert assistant.booking is not None
    assert {"check_availability", "book_appointment"} <= set(assistant.live_actions)
    assert (
        scenarios["transfer_request"]["assistant"] == "eval/fixtures/assistants/transfer_firm.yaml"
    )
    assert scenarios["transfer_request"]["expected_transfer"] == {
        "target_key": "duty_solicitor",
        "status": "no-answer",
    }


def test_score_call_checks_transfer_metrics(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    metrics_path = run_dir / "metrics.json"
    metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
    metrics["transfer"] = {"target_key": "duty_solicitor", "status": "no-answer"}
    metrics_path.write_text(json.dumps(metrics), encoding="utf-8")

    result = score_call(
        run_dir, {"expected_transfer": {"target_key": "duty_solicitor", "status": "no-answer"}}
    )

    assert result["transfer_checks"]["target_key"]["passed"]
    assert result["transfer_checks"]["status"]["passed"]


def test_transfer_scenario_loads_its_assistant_override() -> None:
    scenario = load_scenarios()["transfer_request"]
    assistant_path = Path(__file__).resolve().parents[1] / scenario["assistant"]
    assistant = load_assistant_config(assistant_path)

    assert "transfer_call" in assistant.live_actions
    assert [target.key for target in assistant.transfer_targets] == ["duty_solicitor"]


@pytest.mark.asyncio
async def test_twilio_simulator_posts_signed_transfer_callback(tmp_path, monkeypatch) -> None:
    call_sid = "CA123"
    callback_url = "https://voice.example.com/twilio/transfer-status"
    twiml = (
        '<Response><Dial timeout="20" action="https://voice.example.com/twilio/transfer-status">'
        '<Number statusCallback="https://voice.example.com/twilio/transfer-status?event=answered">'
        "+447700900123</Number></Dial></Response>"
    )
    log_path = tmp_path / "twilio_call_control.jsonl"
    log_path.write_text(
        json.dumps({"call_sid": call_sid, "twiml": twiml}) + "\n",
        encoding="utf-8",
    )
    posted = {}

    class FakeResponse:
        status = 200

        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        async def text(self):
            return "<Response><Hangup /></Response>"

    class FakeSession:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return None

        def post(self, url, *, data, headers):
            posted.update(url=url, data=data, headers=headers)
            return FakeResponse()

    monkeypatch.setattr(twilio_sim, "ClientSession", FakeSession)
    timeline = {"agent_speech": [{"start_seconds": 0.5}]}
    auth_token = "simulator-auth-token"

    await twilio_sim._observe_fake_transfer(
        log_path,
        call_sid,
        {"transfer_dial_status": "no-answer"},
        auth_token,
        time.monotonic() - 1,
        timeline,
    )

    assert posted["url"] == callback_url
    assert posted["data"] == {"CallSid": call_sid, "DialCallStatus": "no-answer"}
    assert posted["headers"]["X-Twilio-Signature"] == compute_signature(
        auth_token, callback_url, posted["data"]
    )
    assert timeline["transfer"]["dial_number"] == "+447700900123"
    assert timeline["transfer"]["callback_status"] == 200
    assert timeline["transfer"]["greeting_to_transfer_seconds"] > 0


def test_score_call_checks_assistant_answers_and_knowledge_gaps(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "transcript.jsonl").write_text(
        "\n".join(
            json.dumps(row)
            for row in [
                {"role": "caller", "text": "Do you offer a discount for NHS workers?"},
                {"role": "assistant", "text": "Our remortgage fee is £595 plus VAT."},
                {"role": "assistant", "text": "There is no free parking near our office."},
                {"role": "assistant", "text": "We do not offer probate services."},
                {"role": "assistant", "text": "Would you like a callback?"},
            ]
        ),
        encoding="utf-8",
    )
    gaps = {
        "unanswered_questions": [
            {
                "question": "Do you offer a discount for NHS workers?",
                "evidence": "Do you offer a discount for NHS workers?",
            }
        ]
    }
    (run_dir / "knowledge_gaps.json").write_text(
        json.dumps(gaps),
        encoding="utf-8",
    )

    result = score_call(
        run_dir,
        {
            "assistant_checks": {
                "fee": "595",
                "parking": "(?i)no free parking",
                "probate": "(?i)do not.*probate",
            },
            "assistant_max_matches": {"callback_offers": {"pattern": "(?i)call ?back", "max": 1}},
            "knowledge_gap_checks": {
                "unanswered": {"pattern": "(?i)NHS|discount", "present": True},
                "answered": {"pattern": "(?i)remortgag|parking|probate", "present": False},
            },
        },
    )

    assert all(check["passed"] for check in result["assistant_checks"].values())
    assert result["assistant_max_matches"]["callback_offers"]["actual"] == 1
    assert result["assistant_max_matches"]["callback_offers"]["passed"]
    assert all(check["passed"] for check in result["knowledge_gap_checks"].values())

    with (run_dir / "transcript.jsonl").open("a", encoding="utf-8") as transcript_file:
        transcript_file.write(
            "\n" + json.dumps({"role": "assistant", "text": "I can call back tomorrow."}) + "\n"
        )
    over_limit = score_call(
        run_dir,
        {"assistant_max_matches": {"callback_offers": {"pattern": "(?i)call ?back", "max": 1}}},
    )
    assert over_limit["assistant_max_matches"]["callback_offers"]["actual"] == 2
    assert not over_limit["assistant_max_matches"]["callback_offers"]["passed"]

    gaps["unanswered_questions"][0]["evidence"] += " Parking details were also requested."
    (run_dir / "knowledge_gaps.json").write_text(
        json.dumps(gaps),
        encoding="utf-8",
    )
    forbidden_evidence = score_call(
        run_dir,
        {
            "knowledge_gap_checks": {
                "forbidden": {
                    "pattern": "(?i)remortgag|parking|probate",
                    "scope": "all",
                    "present": False,
                }
            }
        },
    )
    assert not forbidden_evidence["knowledge_gap_checks"]["forbidden"]["passed"]


def test_score_call_checks_resumed_hold_and_speech_gaps(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "transcript.jsonl").write_text(
        json.dumps({"role": "assistant", "text": "Hello there."}) + "\n",
        encoding="utf-8",
    )
    (run_dir / "caller_timeline.json").write_text(
        json.dumps(
            {
                "number_holds": [{"number_hold_started": 3.0, "resumed": True, "held_secs": 1.4}],
                "utterances": [
                    {
                        "segments": [
                            {"start_seconds": 1.0, "end_seconds": 2.0},
                            {"start_seconds": 3.4, "end_seconds": 4.0},
                        ]
                    }
                ],
                "agent_speech": [{"start_seconds": 0.5, "end_seconds": 0.8}],
            }
        ),
        encoding="utf-8",
    )

    result = score_call(
        run_dir,
        {
            "assistant_not_matches": "(?i)call ?back",
            "task_checks": {"number_hold_resumed": True},
            "no_agent_speech_between_segments": True,
        },
    )

    assert result["assistant_not_matches"]["unexpected_match"]["passed"]
    assert result["task_checks"]["number_hold_resumed"]["passed"]
    assert result["task_checks"]["no_agent_speech_between_segments"]["passed"]


def test_aggregate_calls_and_summary_use_requested_latency_and_check_stats() -> None:
    calls = [
        {
            "stt": "assemblyai",
            "tts": "speechify",
            "tts_voice": "beatrice_32",
            "llm": "google",
            "llm_model": "gemini-2.5-flash",
            "scenario": "employment",
            "rep": 1,
            "checks_passed": 4,
            "checks_total": 5,
            "turn_latency_summary": {"p50_seconds": 0.6, "p95_seconds": 1.1},
            "turn_end_latency_summary": {"p50_seconds": 0.2, "p95_seconds": 0.4},
            "turn_latencies_seconds": [0.5, 1.0],
            "server_disconnect_detected": True,
            "seconds_from_last_agent_speech_end_to_disconnect": 2.0,
            "estimated_cost": {"currency": "USD", "total": 0.08},
            "ended_via_end_call": True,
        },
        {
            "stt": "assemblyai",
            "tts": "speechify",
            "tts_voice": "beatrice_32",
            "llm": "google",
            "llm_model": "gemini-2.5-flash",
            "scenario": "employment",
            "rep": 2,
            "checks_passed": 5,
            "checks_total": 5,
            "turn_latency_summary": {"p50_seconds": 0.8, "p95_seconds": 1.4},
            "turn_end_latency_summary": {"p50_seconds": 0.3, "p95_seconds": 0.5},
            "turn_latencies_seconds": [1.5, 2.0],
            "server_disconnect_detected": False,
            "estimated_cost": {"currency": "USD", "total": 0.1},
            "ended_via_end_call": False,
        },
    ]

    aggregate = aggregate_calls(calls)
    summary = render_summary(calls)

    assert aggregate["median_p50_latency_seconds"] == 0.7
    assert aggregate["max_p95_latency_seconds"] == 1.4
    assert aggregate["all_turns_p95_latency_seconds"] == pytest.approx(1.925)
    assert aggregate["checks_passed"] == 9
    assert aggregate["checks_total"] == 10
    assert aggregate["mean_cost"] == 0.09
    assert "google (gemini-2.5-flash)" in summary
    assert "Voice" in summary
    assert "Intake live / post / merged" in summary
    assert "Review" in summary
    assert "Task sources" in summary
    assert "Smart turn stop" in summary
    assert "LLM TTFB median" in summary
    assert "all-turn p95" in summary
    assert "Server disconnect" in summary
    assert "Speech-end→disconnect" in summary
    assert "beatrice_32" in summary
    assert "| Aggregate " in summary
    assert "9/10" in summary


def test_false_urgent_check_passes_when_no_urgent_task_exists(tmp_path) -> None:
    run_dir = tmp_path / "fixture-call"
    shutil.copytree(FIXTURE, run_dir)
    (run_dir / "tasks.json").write_text(json.dumps([{"type": "end_call"}]), encoding="utf-8")

    result = score_call(run_dir, {"task_checks": {"urgent": False}})

    assert result["task_checks"]["urgent"] == {
        "expected": False,
        "actual": False,
        "passed": True,
    }
