from __future__ import annotations

import argparse
import asyncio
import inspect
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Awaitable, Callable, Mapping, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import aiohttp
import httpx
import yaml
from inbound_core.call_report import RouteResponse

from eval.run_matrix import (
    FAKE_API_TOKEN,
    PROVIDER_ERROR,
    _existing_run_dirs,
    _newest_run_dir,
    _running_fake_api,
    _start_runner,
    _stop_runner,
    _wait_for_extraction,
)
from eval.score import score_call
from eval.synth_callers import AUDIO_DIR, synthesize_scenarios
from eval.twilio_sim import run_twilio_call
from inbound_voice.api_client import ApiSettings, api_settings_from_env
from inbound_voice.config import VOICE_ROOT, load_assistant_config
from inbound_voice.providers import LLM_PROVIDERS, STT_PROVIDERS, TTS_PROVIDERS

RUN_CASES_DIR = Path(__file__).with_name("results") / "cases"
CALLER_ID = "07700900000"
DEFAULT_CALLER_VOICE = "catriona"
DEFAULT_ROUTE_TENANT_ID = UUID("10000000-0000-0000-0000-000000000001")
DEFAULT_ROUTE_ASSISTANT_ID = UUID("20000000-0000-0000-0000-000000000002")

RunCase = Callable[
    [str, dict[str, Any], RouteResponse | None],
    dict[str, Any] | Awaitable[dict[str, Any]],
]


async def fetch_cases(settings: ApiSettings) -> list[dict[str, Any]]:
    async with httpx.AsyncClient(
        base_url=settings.base_url,
        headers={"X-Service-Token": settings.service_token},
        timeout=30,
    ) as client:
        response = await client.get("/internal/eval-cases")
        response.raise_for_status()
        payload = response.json()
    return payload["items"]


async def post_run(settings: ApiSettings, payload: dict[str, Any]) -> dict[str, Any]:
    async with httpx.AsyncClient(
        base_url=settings.base_url,
        headers={"X-Service-Token": settings.service_token},
        timeout=30,
    ) as client:
        response = await client.post("/internal/eval-runs", json=payload)
        response.raise_for_status()
        return response.json()


def case_to_scenario(case: Mapping[str, Any]) -> tuple[dict[str, Any] | None, list[str]]:
    failures: list[str] = []
    utterances = case.get("caller_utterances") or []
    if not utterances:
        failures.append("no caller utterances")
    route_data = case.get("route")
    assistant_id = case.get("assistant_id")
    if assistant_id is not None and route_data is None:
        failures.append("assistant not found")
    checks = case.get("checks") or {}
    for group in ("transcript_checks", "assistant_checks", "intake_checks"):
        patterns = checks.get(group, {})
        for name, pattern in patterns.items():
            try:
                re.compile(pattern)
            except (re.error, TypeError):
                failures.append(f"invalid pattern for {group}.{name}")
    if failures:
        return None, failures

    scenario = {
        "caller_voice": os.environ.get("EVAL_CALLER_VOICE", DEFAULT_CALLER_VOICE),
        "caller_id": CALLER_ID,
        "utterances": list(utterances),
        "transcript_checks": checks.get("transcript_checks", {}),
        "assistant_checks": checks.get("assistant_checks", {}),
        "intake_checks": checks.get("intake_checks", {}),
    }
    return scenario, []


def case_failures(scored: Mapping[str, Any]) -> list[str]:
    failures = []
    for group in ("transcript_checks", "assistant_checks", "intake_checks"):
        for name, check in (scored.get(group) or {}).items():
            if check.get("passed"):
                continue
            pattern = check.get("pattern", "")
            failure = f"{group}.{name}: /{pattern}/ not matched"
            if group == "intake_checks":
                failure += f" (got {check.get('value')!r})"
            failures.append(failure)
    caller_error = scored.get("caller_error")
    if caller_error:
        failures.append(f"caller: {caller_error}")
    return failures


def case_metrics(scored: Mapping[str, Any]) -> dict[str, Any]:
    latency = scored.get("turn_latency_summary") or {}
    cost = scored.get("estimated_cost") or {}
    return {
        "checks_passed": scored.get("checks_passed", 0),
        "checks_total": scored.get("checks_total", 0),
        "response_p50_s": latency.get("p50_seconds"),
        "response_p95_s": latency.get("p95_seconds"),
        "max_response_s": latency.get("max_seconds"),
        "interruptions": scored.get("interruptions"),
        "call_duration_s": scored.get("call_duration_seconds"),
        "estimated_cost": cost.get("total"),
        "run_dir": scored.get("run_dir"),
    }


async def run_cases(
    cases: Sequence[Mapping[str, Any]],
    *,
    run_case: RunCase,
) -> dict[str, Any]:
    results = []
    provider_error_seen = False
    for case in cases:
        case_id = UUID(str(case["id"]))
        if provider_error_seen:
            results.append(
                {
                    "eval_case_id": str(case_id),
                    "passed": False,
                    "failures": ["not run: provider error"],
                    "metrics": {},
                }
            )
            continue

        scenario, failures = case_to_scenario(case)
        if scenario is None:
            results.append(
                {
                    "eval_case_id": str(case_id),
                    "passed": False,
                    "failures": failures,
                    "metrics": {},
                }
            )
            continue

        route_data = case.get("route")
        route = RouteResponse.model_validate(route_data) if route_data is not None else None
        name = f"case-{case_id}"
        scored_result = run_case(name, scenario, route)
        scored = await scored_result if inspect.isawaitable(scored_result) else scored_result
        provider_error = scored.get("provider_error")
        if provider_error:
            failures = [f"provider error: {provider_error}"]
            provider_error_seen = True
        else:
            failures = case_failures(scored)
        results.append(
            {
                "eval_case_id": str(case_id),
                "passed": not failures,
                "failures": failures,
                "metrics": case_metrics(scored),
            }
        )
    passed = sum(result["passed"] for result in results)
    return {
        "summary": {
            "cases": len(results),
            "passed": passed,
            "failed": len(results) - passed,
        },
        "results": results,
    }


def _default_route() -> RouteResponse:
    assistant_path = os.environ.get("ASSISTANT_CONFIG") or (
        VOICE_ROOT / "config" / "assistants" / "demo_firm.yaml"
    )
    assistant = load_assistant_config(assistant_path)
    return RouteResponse(
        tenant_id=DEFAULT_ROUTE_TENANT_ID,
        assistant_id=DEFAULT_ROUTE_ASSISTANT_ID,
        config=assistant,
        digest=assistant.knowledge.digest_content,
    )


async def _set_fake_route(api_base_url: str, route: RouteResponse) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_base_url}/_fake/route",
            headers={"X-Service-Token": FAKE_API_TOKEN},
            json=route.model_dump(mode="json"),
        ) as response:
            response.raise_for_status()


async def _run_live_cases(
    cases: Sequence[Mapping[str, Any]],
    scenarios_path: Path,
    result_dir: Path,
    *,
    stt: str,
    tts: str,
    llm: str,
    port: int,
) -> dict[str, Any]:
    valid_scenarios = {}
    for case in cases:
        scenario, failures = case_to_scenario(case)
        if scenario is not None:
            valid_scenarios[f"case-{UUID(str(case['id']))}"] = scenario
    scenarios_path.write_text(
        yaml.safe_dump({"scenarios": valid_scenarios}, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    if valid_scenarios:
        await synthesize_scenarios(
            list(valid_scenarios),
            output_dir=AUDIO_DIR,
            scenarios_path=scenarios_path,
        )

    async def run_one(
        name: str,
        scenario: dict[str, Any],
        route: RouteResponse | None,
    ) -> dict[str, Any]:
        selected_route = route or default_route
        await _set_fake_route(api_base_url, selected_route)
        before = _existing_run_dirs()
        scratch = result_dir / "calls" / name
        timeline = await run_twilio_call(
            name,
            scratch,
            base_url=f"http://127.0.0.1:{port}",
            auth_token="test-auth-token",
            to_number="+441234567890",
            from_number=scenario["caller_id"],
            audio_dir=AUDIO_DIR,
            scenarios_path=scenarios_path,
            fake_call_log_path=result_dir / "twilio_call_control.jsonl",
        )
        run_dir = await _newest_run_dir(before)
        if run_dir is not None:
            await _wait_for_extraction(run_dir)
            for filename in ("bot_audio.wav", "caller_audio.wav", "caller_timeline.json"):
                source = scratch / filename
                if source.exists():
                    shutil.copy2(source, run_dir / filename)
            scored = score_call(run_dir, scenario)
        else:
            scored = score_call(scratch, scenario)
        scored["run_dir"] = str(run_dir) if run_dir is not None else None
        scored["caller_error"] = timeline.get("error")
        error_lines = [line for line in output_lines if PROVIDER_ERROR.search(line)]
        if error_lines:
            scored["provider_error"] = error_lines[-1]
        return scored

    if not valid_scenarios:
        return await run_cases(cases, run_case=lambda *_: {})

    default_route = _default_route()
    async with _running_fake_api(route_response=default_route) as api_base_url:
        with tempfile.TemporaryDirectory(prefix="inbound-eval-spool-") as spool_dir:
            process, output_task, output_lines = await _start_runner(
                stt,
                tts,
                llm,
                speechify_voice=os.environ.get("TTS_VOICE"),
                transport="twilio",
                api_base_url=api_base_url,
                service_token=FAKE_API_TOKEN,
                phone_port=port,
                python_command=(sys.executable,),
                extra_env={"VOICE_SPOOL_DIR": spool_dir, "REDIS_URL": ""},
            )
            try:
                return await run_cases(cases, run_case=run_one)
            finally:
                await _stop_runner(process, output_task)


def _git_sha() -> str | None:
    configured = os.environ.get("GIT_SHA", "").strip()
    if configured:
        return configured
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=VOICE_ROOT,
            check=True,
            capture_output=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return result.stdout.strip() or None


def _results_directory() -> Path:
    timestamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    path = RUN_CASES_DIR / timestamp
    path.mkdir(parents=True, exist_ok=False)
    return path


def _summary_markdown(cases: Sequence[Mapping[str, Any]], payload: Mapping[str, Any]) -> str:
    names = {str(case["id"]): case.get("name", f"case-{case['id']}") for case in cases}
    lines = ["| Case | Passed | Failures |", "| --- | --- | --- |"]
    for result in payload["results"]:
        name = names.get(result["eval_case_id"], result["eval_case_id"])
        failures = "; ".join(result["failures"]) or "—"
        lines.append(f"| {name} | {result['passed']} | {failures} |")
    summary = payload["summary"]
    lines.extend(
        [
            "",
            f"Cases: {summary['cases']}; passed: {summary['passed']}; failed: {summary['failed']}.",
        ]
    )
    return "\n".join(lines) + "\n"


def _single_provider(
    name: str,
    env_value: str | None,
    fallback: str,
    choices: Sequence[str],
) -> str:
    value = (env_value or os.environ.get(name) or fallback).strip()
    if value not in choices:
        raise ValueError(f"{name} must be one of: {', '.join(choices)}")
    return value


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run backend-configured voice regression evals.")
    parser.add_argument("--trigger", default=os.environ.get("EVAL_TRIGGER", "manual"))
    parser.add_argument("--case", nargs="+", dest="case_ids")
    parser.add_argument(
        "--stt",
        choices=STT_PROVIDERS,
        default=_single_provider("STT_PROVIDER", None, "assemblyai", STT_PROVIDERS),
    )
    parser.add_argument(
        "--tts",
        choices=TTS_PROVIDERS,
        default=_single_provider("TTS_PROVIDER", None, "speechify", TTS_PROVIDERS),
    )
    parser.add_argument(
        "--llm",
        choices=LLM_PROVIDERS,
        default=_single_provider("LLM_PROVIDER", None, "google", LLM_PROVIDERS),
    )
    parser.add_argument("--port", type=int, default=7871)
    parser.add_argument("--no-post", action="store_true")
    parser.add_argument("--list", action="store_true")
    return parser


async def _run(arguments: argparse.Namespace) -> int:
    settings = api_settings_from_env(os.environ)
    if settings is None:
        print(
            "API_BASE_URL and VOICE_SERVICE_TOKEN are required to run regression evals.",
            file=sys.stderr,
        )
        return 2
    try:
        cases = await fetch_cases(settings)
    except (httpx.HTTPError, ValueError) as error:
        print(f"Could not fetch eval cases: {error}", file=sys.stderr)
        return 2
    if arguments.list:
        for case in cases:
            print(f"{case['id']}\t{case['name']}")
        return 0
    if arguments.case_ids:
        wanted = set(arguments.case_ids)
        cases = [case for case in cases if str(case["id"]) in wanted]

    result_dir = _results_directory()
    started_at = datetime.now(UTC)
    try:
        result = await _run_live_cases(
            cases,
            result_dir / "scenarios.yaml",
            result_dir,
            stt=arguments.stt,
            tts=arguments.tts,
            llm=arguments.llm,
            port=arguments.port,
        )
    except Exception as error:
        print(f"Regression eval execution failed: {error}", file=sys.stderr)
        return 2
    payload = {
        "started_at": started_at.isoformat(),
        "finished_at": datetime.now(UTC).isoformat(),
        "git_sha": _git_sha(),
        "trigger": arguments.trigger,
        **result,
    }
    (result_dir / "results.json").write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    (result_dir / "summary.md").write_text(
        _summary_markdown(cases, payload),
        encoding="utf-8",
    )
    print(f"Results: {result_dir}")
    print((result_dir / "summary.md").read_text(encoding="utf-8"), end="")
    if not arguments.no_post:
        try:
            created = await post_run(settings, payload)
        except httpx.HTTPError as error:
            print(f"Could not post eval run: {error}\nLocal results: {result_dir}", file=sys.stderr)
            return 2
        print(f"Posted eval run {created['eval_run_id']}")
    return 1 if result["summary"]["failed"] else 0


def main() -> None:
    try:
        parser = build_parser()
    except ValueError as error:
        argparse.ArgumentParser(description="Run backend-configured voice regression evals.").error(
            str(error)
        )
    arguments = parser.parse_args()
    try:
        exit_code = asyncio.run(_run(arguments))
    except ValueError as error:
        parser.error(str(error))
    raise SystemExit(exit_code)


if __name__ == "__main__":
    main()
