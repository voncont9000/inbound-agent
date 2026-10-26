import argparse
import asyncio
import json
import socket
import sys
from pathlib import Path
from uuid import uuid4

import pytest
import uvicorn
from fastapi import FastAPI, Header, HTTPException
from fastapi.responses import JSONResponse
from test_api_client import _route_response

import eval.run_cases as run_cases_module
from eval.run_cases import (
    _git_sha,
    _run,
    build_parser,
    case_failures,
    case_metrics,
    case_to_scenario,
    fetch_cases,
    main,
    post_run,
    run_cases,
)
from eval.score import score_call
from inbound_voice.api_client import ApiSettings


def _case(**updates):
    case = {
        "id": str(uuid4()),
        "name": "General support",
        "assistant_id": None,
        "source_conversation_id": None,
        "caller_utterances": ["I need some help."],
        "checks": {
            "transcript_checks": {"caller_greeting": "help"},
            "assistant_checks": {"assistant_greeting": "hello"},
            "intake_checks": {"caller_name": "Alex"},
        },
        "route": None,
    }
    return {**case, **updates}


def test_case_to_scenario_maps_case_and_route(monkeypatch) -> None:
    monkeypatch.delenv("EVAL_CALLER_VOICE", raising=False)
    route = _route_response()
    case = _case(assistant_id=str(route.assistant_id), route=route.model_dump(mode="json"))

    scenario, failures = case_to_scenario(case)

    assert scenario == {
        "caller_voice": "catriona",
        "caller_id": "07700900000",
        "utterances": ["I need some help."],
        "transcript_checks": {"caller_greeting": "help"},
        "assistant_checks": {"assistant_greeting": "hello"},
        "intake_checks": {"caller_name": "Alex"},
    }
    assert failures == []


@pytest.mark.parametrize(
    ("updates", "expected"),
    [
        ({"caller_utterances": []}, "no caller utterances"),
        ({"assistant_id": str(uuid4()), "route": None}, "assistant not found"),
        (
            {
                "checks": {
                    "transcript_checks": {"broken": "["},
                    "assistant_checks": {},
                    "intake_checks": {},
                }
            },
            "invalid pattern for transcript_checks.broken",
        ),
        (
            {
                "checks": {
                    "transcript_checks": {},
                    "assistant_checks": {"broken": "("},
                    "intake_checks": {},
                }
            },
            "invalid pattern for assistant_checks.broken",
        ),
        (
            {
                "checks": {
                    "transcript_checks": {},
                    "assistant_checks": {},
                    "intake_checks": {"broken": "["},
                }
            },
            "invalid pattern for intake_checks.broken",
        ),
    ],
)
def test_case_to_scenario_rejects_invalid_cases(updates, expected) -> None:
    scenario, failures = case_to_scenario(_case(**updates))

    assert scenario is None
    assert expected in failures


def test_case_failures_and_metrics_from_score_call_output(tmp_path: Path) -> None:
    (tmp_path / "transcript.jsonl").write_text(
        "\n".join(
            [
                json.dumps({"role": "caller", "text": "I need help."}),
                json.dumps({"role": "assistant", "text": "Hello Alex."}),
            ]
        )
        + "\n",
        encoding="utf-8",
    )
    (tmp_path / "intake.json").write_text(
        json.dumps({"fields": {"caller_name": {"value": "Sam"}}}),
        encoding="utf-8",
    )
    (tmp_path / "metrics.json").write_text(
        json.dumps(
            {
                "turn_latency_summary": {
                    "p50_seconds": 0.7,
                    "p95_seconds": 1.4,
                    "max_seconds": 1.8,
                },
                "interruption_count": 2,
                "call_duration_seconds": 120,
                "turns": [],
            }
        ),
        encoding="utf-8",
    )
    scenario = {
        "transcript_checks": {"caller": "help"},
        "assistant_checks": {"greeting": "welcome"},
        "intake_checks": {"caller_name": "Alex"},
    }

    scored = score_call(tmp_path, scenario)
    scored["caller_error"] = "caller disconnected"
    failures = case_failures(scored)
    metrics = case_metrics(scored)

    assert failures == [
        "assistant_checks.greeting: /welcome/ not matched",
        "intake_checks.caller_name: /Alex/ not matched (got 'Sam')",
        "caller: caller disconnected",
    ]
    assert metrics == {
        "checks_passed": scored["checks_passed"],
        "checks_total": scored["checks_total"],
        "response_p50_s": 0.7,
        "response_p95_s": 1.4,
        "max_response_s": 1.8,
        "interruptions": 2,
        "call_duration_s": 120,
        "estimated_cost": scored["estimated_cost"]["total"],
        "run_dir": str(tmp_path),
    }


@pytest.mark.asyncio
async def test_run_cases_skips_invalid_cases_and_stops_after_provider_error() -> None:
    skipped = _case(caller_utterances=[])
    passed = _case()
    provider_error = _case()
    after_error = _case()
    calls = []

    def run_one(name, scenario, route):
        calls.append(name)
        if name == f"case-{provider_error['id']}":
            return {"provider_error": "HTTP 401 provider unauthorized"}
        return {
            "transcript_checks": {"caller_greeting": {"pattern": "help", "passed": True}},
            "assistant_checks": {},
            "intake_checks": {},
        }

    result = await run_cases(
        [skipped, passed, provider_error, after_error],
        run_case=run_one,
    )

    assert calls == [f"case-{passed['id']}", f"case-{provider_error['id']}"]
    assert result["summary"] == {"cases": 4, "passed": 1, "failed": 3}
    assert result["results"][0]["failures"] == ["no caller utterances"]
    assert result["results"][2]["failures"] == ["provider error: HTTP 401 provider unauthorized"]
    assert result["results"][3]["failures"] == ["not run: provider error"]


@pytest.mark.asyncio
async def test_run_cases_executes_cases_one_at_a_time() -> None:
    active = 0
    maximum_active = 0

    async def run_one(*_args):
        nonlocal active, maximum_active
        active += 1
        maximum_active = max(maximum_active, active)
        await asyncio.sleep(0)
        active -= 1
        return {"transcript_checks": {}, "assistant_checks": {}, "intake_checks": {}}

    result = await run_cases([_case(), _case(), _case()], run_case=run_one)

    assert maximum_active == 1
    assert result["summary"] == {"cases": 3, "passed": 3, "failed": 0}


@pytest.mark.asyncio
async def test_execution_failure_posts_a_failed_run_with_every_case_marked_failed(
    monkeypatch, tmp_path: Path
) -> None:
    cases = [_case(), _case()]
    settings = ApiSettings("http://api.example.test", "token")
    posted = []

    monkeypatch.setattr(run_cases_module, "api_settings_from_env", lambda _env: settings)

    async def fetch_cases(_settings):
        return cases

    async def fail_execution(*_args, **_kwargs):
        raise RuntimeError("Speechify synthesis failed (429): rate limited")

    async def post_failed_run(_settings, payload):
        posted.append(payload)
        return {"eval_run_id": str(uuid4())}

    monkeypatch.setattr(run_cases_module, "fetch_cases", fetch_cases)
    monkeypatch.setattr(run_cases_module, "_run_live_cases", fail_execution)
    monkeypatch.setattr(run_cases_module, "post_run", post_failed_run)
    monkeypatch.setattr(run_cases_module, "_results_directory", lambda: tmp_path)
    arguments = argparse.Namespace(
        list=False,
        case_ids=None,
        trigger="deploy",
        stt="assemblyai",
        tts="speechify",
        llm="google",
        port=7871,
        no_post=False,
    )

    exit_code = await _run(arguments)

    assert exit_code == 2
    assert len(posted) == 1
    assert posted[0]["summary"] == {"cases": 2, "passed": 0, "failed": 2}
    assert [result["eval_case_id"] for result in posted[0]["results"]] == [
        case["id"] for case in cases
    ]
    assert all(not result["passed"] for result in posted[0]["results"])
    assert all(
        "Speechify synthesis failed" in result["failures"][0] for result in posted[0]["results"]
    )
    saved_payload = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved_payload == posted[0]


def test_cli_defaults_follow_environment(monkeypatch) -> None:
    monkeypatch.setenv("EVAL_TRIGGER", "nightly")
    monkeypatch.setenv("STT_PROVIDER", "assemblyai")
    monkeypatch.setenv("TTS_PROVIDER", "speechify")
    monkeypatch.setenv("LLM_PROVIDER", "google")

    args = build_parser().parse_args([])

    assert args.trigger == "nightly"
    assert args.stt == "assemblyai"
    assert args.tts == "speechify"
    assert args.llm == "google"
    assert args.port == 7871
    assert args.no_post is False
    assert args.list is False


@pytest.mark.parametrize(
    "variable",
    ["STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER"],
)
def test_invalid_provider_environment_exits_with_parser_error(
    monkeypatch, capsys, variable
) -> None:
    for provider_variable in ("STT_PROVIDER", "TTS_PROVIDER", "LLM_PROVIDER"):
        monkeypatch.delenv(provider_variable, raising=False)
    monkeypatch.setenv(variable, "invalid")
    monkeypatch.setattr(sys, "argv", ["eval.run_cases"])

    with pytest.raises(SystemExit) as error:
        main()

    assert error.value.code == 2
    assert f"{variable} must be one of:" in capsys.readouterr().err


def test_git_sha_uses_environment(monkeypatch) -> None:
    monkeypatch.setenv("GIT_SHA", "from-env")

    assert _git_sha() == "from-env"


@pytest.mark.asyncio
async def test_fetch_and_post_use_local_uvicorn_app_and_service_token() -> None:
    expected_cases = [{"id": str(uuid4()), "name": "Local case"}]
    app = FastAPI()
    app.state.posted = None

    async def check_token(x_service_token: str | None) -> None:
        if x_service_token != "local-eval-token":
            raise HTTPException(status_code=401)

    @app.get("/internal/eval-cases")
    async def cases(x_service_token: str | None = Header(default=None)):
        await check_token(x_service_token)
        return {"items": expected_cases}

    @app.post("/internal/eval-runs")
    async def create_run(payload: dict, x_service_token: str | None = Header(default=None)):
        await check_token(x_service_token)
        app.state.posted = payload
        return JSONResponse({"eval_run_id": str(uuid4())}, status_code=201)

    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        port = server_socket.getsockname()[1]
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    server_task = asyncio.create_task(server.serve())
    try:
        async with asyncio.timeout(5):
            while not server.started:
                await asyncio.sleep(0.01)
        settings = ApiSettings(f"http://127.0.0.1:{port}", "local-eval-token")

        assert await fetch_cases(settings) == expected_cases
        payload = {"trigger": "test", "summary": {"cases": 0, "passed": 0, "failed": 0}}
        created = await post_run(settings, payload)

        assert created["eval_run_id"]
        assert app.state.posted == payload
    finally:
        server.should_exit = True
        await server_task
