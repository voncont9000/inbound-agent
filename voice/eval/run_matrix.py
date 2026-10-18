from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import shutil
import signal
import socket
import tempfile
import time
from collections.abc import Sequence
from contextlib import asynccontextmanager
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import aiohttp
import uvicorn
from inbound_core.call_report import RouteResponse

from bot import SMART_TURN_STOP_DEFAULT_SECS
from eval.caller import run_call
from eval.fake_api import create_fake_api
from eval.score import render_summary, score_call
from eval.synth_callers import AUDIO_DIR, load_scenarios, synthesize_scenarios
from eval.twilio_sim import run_twilio_call
from inbound_voice.config import AssistantConfig, load_assistant_config
from inbound_voice.providers import LLM_DEFAULT_MODELS, LLM_PROVIDERS, STT_PROVIDERS, TTS_PROVIDERS

VOICE_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = VOICE_ROOT / "runs"
RESULTS_DIR = Path(__file__).with_name("results")
RUNNER_URL = "http://127.0.0.1:7860"
PHONE_SERVER_URL = "http://127.0.0.1:7861"
FAKE_API_TOKEN = "dev-token"
FAKE_API_TO_NUMBER = "+441234567890"
DEFAULT_SPEECHIFY_VOICE = "beatrice_32"
DEFAULT_ELEVENLABS_VOICE = "MzqUf1HbJ8UmQ0wUsx2p"
API_KEY_NAMES = (
    "OPENROUTER_API_KEY",
    "GOOGLE_API_KEY",
    "ASSEMBLYAI_API_KEY",
    "SONIOX_API_KEY",
    "SPEECHIFY_API_KEY",
    "ELEVENLABS_API_KEY",
    "DEEPGRAM_API_KEY",
    "VOICE_SERVICE_TOKEN",
    "TWILIO_AUTH_TOKEN",
)
PROVIDER_ERROR = re.compile(
    r"(?:(?:http(?:/\d(?:\.\d)?)?(?:\s+(?:status(?:\s+code)?|code))?|"
    r"status(?:\s+code)?|response(?:\s+(?:status|code))?|error(?:\s+code)?)"
    r"\s*[=:]?\s*(?<![\d.:])\b(?:400|401|403|429|5\d\d)\b)|"
    r"unauthorized|forbidden|authenticationerror|invalid[_ ]api[_ ]key|"
    r"model.{0,40}(not found|unsupported)|voice.{0,40}(not found|invalid)",
    re.IGNORECASE,
)


async def _read_runner_output(
    stream: asyncio.StreamReader,
    process_env: dict[str, str],
    lines: list[str],
) -> None:
    secrets = [process_env[name] for name in API_KEY_NAMES if process_env.get(name)]
    while line := await stream.readline():
        text = line.decode("utf-8", errors="replace")
        for secret in secrets:
            text = text.replace(secret, "[REDACTED]")
        lines.append(text.rstrip())
        print(text, end="", flush=True)


def _configure_tts_voice(
    process_env: dict[str, str],
    tts: str,
    speechify_voice: str | None = None,
    elevenlabs_voice: str | None = None,
) -> None:
    if tts == "speechify":
        process_env["TTS_VOICE"] = speechify_voice or DEFAULT_SPEECHIFY_VOICE
    elif tts == "elevenlabs":
        process_env["TTS_VOICE"] = elevenlabs_voice or DEFAULT_ELEVENLABS_VOICE


def _configure_llm_provider(process_env: dict[str, str], llm: str) -> None:
    process_env["LLM_PROVIDER"] = llm
    process_env.pop("LLM_MODEL", None)


def _configure_caller_id_override(process_env: dict[str, str], caller_id: str | None) -> None:
    process_env.pop("CALLER_ID_OVERRIDE", None)
    if caller_id:
        process_env["CALLER_ID_OVERRIDE"] = caller_id


async def _start_runner(
    stt: str,
    tts: str,
    llm: str,
    speechify_voice: str | None = None,
    elevenlabs_voice: str | None = None,
    smart_turn_stop_secs: float = SMART_TURN_STOP_DEFAULT_SECS,
    knowledge_digest_path: str | None = None,
    caller_id_override: str | None = None,
    transport: str = "webrtc",
    api_base_url: str | None = None,
    service_token: str | None = None,
    assistant_path: str | None = None,
    twilio_fake_log_path: str | None = None,
) -> tuple[asyncio.subprocess.Process, asyncio.Task[None], list[str]]:
    process_env = os.environ.copy()
    process_env["STT_PROVIDER"] = stt
    process_env["TTS_PROVIDER"] = tts
    _configure_tts_voice(process_env, tts, speechify_voice, elevenlabs_voice)
    _configure_llm_provider(process_env, llm)
    process_env["SMART_TURN_STOP_SECS"] = str(smart_turn_stop_secs)
    if knowledge_digest_path:
        process_env["KNOWLEDGE_DIGEST_PATH"] = knowledge_digest_path
    if assistant_path:
        process_env["ASSISTANT_CONFIG"] = str(VOICE_ROOT / assistant_path)
    _configure_caller_id_override(process_env, caller_id_override)
    if api_base_url is not None and service_token is not None:
        process_env["API_BASE_URL"] = api_base_url
        process_env["VOICE_SERVICE_TOKEN"] = service_token
        process_env["WEB_CALL_TO_NUMBER"] = FAKE_API_TO_NUMBER
    if transport == "twilio":
        process_env["TWILIO_AUTH_TOKEN"] = "test-auth-token"
        process_env["TWILIO_ACCOUNT_SID"] = ""
        process_env["PUBLIC_BASE_URL"] = ""
        process_env["TWILIO_CALL_CONTROL"] = "fake"
        if twilio_fake_log_path:
            process_env["TWILIO_FAKE_CALL_LOG"] = twilio_fake_log_path
        command = ["uv", "run", "phone_server.py", "--port", "7861"]
        readiness_url, readiness_path = PHONE_SERVER_URL, "/healthz"
    else:
        command = ["uv", "run", "bot.py"]
        readiness_url, readiness_path = RUNNER_URL, "/client/"
    process = await asyncio.create_subprocess_exec(
        *command,
        cwd=VOICE_ROOT,
        env=process_env,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.STDOUT,
        start_new_session=True,
    )
    assert process.stdout is not None
    lines: list[str] = []
    output_task = asyncio.create_task(_read_runner_output(process.stdout, process_env, lines))
    try:
        await _wait_for_runner(
            process,
            lines,
            base_url=readiness_url,
            path=readiness_path,
        )
    except Exception:
        await _stop_runner(process, output_task)
        raise
    return process, output_task, lines


async def _wait_for_runner(
    process: asyncio.subprocess.Process,
    lines: list[str],
    timeout: float = 45,
    *,
    base_url: str = RUNNER_URL,
    path: str = "/client/",
) -> None:
    await _wait_for_http(base_url, path, timeout, process=process, lines=lines)


async def _wait_for_http(
    base_url: str,
    path: str,
    timeout: float,
    *,
    process: asyncio.subprocess.Process | None = None,
    lines: list[str] | None = None,
    headers: dict[str, str] | None = None,
) -> None:
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
        while time.monotonic() < deadline:
            if process is not None and process.returncode is not None:
                break
            try:
                async with session.get(
                    f"{base_url.rstrip('/')}{path}",
                    headers=headers,
                ) as response:
                    if response.status == 200:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(0.25)
    tail = "\n".join((lines or [])[-30:])
    if process is None or process.returncode is None:
        raise TimeoutError(f"Service did not serve {path} within {timeout}s.\n{tail}")
    raise RuntimeError(f"Service exited with status {process.returncode}.\n{tail}")


@asynccontextmanager
async def _running_fake_api(
    to_number: str = FAKE_API_TO_NUMBER,
    knowledge_digest_path: str | None = None,
):
    with socket.socket() as server_socket:
        server_socket.bind(("127.0.0.1", 0))
        port = server_socket.getsockname()[1]
    assistant = load_assistant_config(VOICE_ROOT / "config" / "assistants" / "demo_firm.yaml")
    route_response = RouteResponse(
        tenant_id=UUID("10000000-0000-0000-0000-000000000001"),
        assistant_id=UUID("20000000-0000-0000-0000-000000000002"),
        config=assistant,
        digest=_fake_api_digest(assistant, knowledge_digest_path),
    )
    app = create_fake_api(route_response, FAKE_API_TOKEN, to_number=to_number)
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    api_url = f"http://127.0.0.1:{port}"
    try:
        await _wait_for_http(
            api_url,
            "/_fake/state",
            10,
            headers={"X-Service-Token": FAKE_API_TOKEN},
        )
        yield api_url
    finally:
        server.should_exit = True
        await asyncio.gather(task, return_exceptions=True)


def _fake_api_digest(
    assistant: AssistantConfig,
    knowledge_digest_path: str | Path | None,
) -> str | None:
    if knowledge_digest_path is not None:
        return Path(knowledge_digest_path).read_text(encoding="utf-8")
    return assistant.knowledge.digest_content


async def _fake_api_state(api_url: str) -> dict[str, Any]:
    async with aiohttp.ClientSession() as session:
        async with session.get(
            f"{api_url}/_fake/state",
            headers={"X-Service-Token": FAKE_API_TOKEN},
        ) as response:
            response.raise_for_status()
            return await response.json()


async def _set_fake_assistant(api_base_url: str, assistant_path: str) -> None:
    async with aiohttp.ClientSession() as session:
        async with session.post(
            f"{api_base_url}/_fake/assistant",
            json={"assistant": assistant_path},
            headers={"X-Service-Token": FAKE_API_TOKEN},
        ) as response:
            response.raise_for_status()


async def _wait_for_fake_report(
    api_url: str,
    calls_before: int,
    timeout: float = 15,
) -> tuple[bool, str | None]:
    deadline = time.monotonic() + timeout
    external_call_id = None
    while time.monotonic() < deadline:
        state = await _fake_api_state(api_url)
        new_calls = state["calls"][calls_before:]
        if new_calls:
            external_call_id = new_calls[-1]["external_call_id"]
            if any(report["external_call_id"] == external_call_id for report in state["reports"]):
                return True, external_call_id
        await asyncio.sleep(0.25)
    return False, external_call_id


async def _stop_runner(
    process: asyncio.subprocess.Process,
    output_task: asyncio.Task[None] | None,
) -> None:
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        pass
    try:
        await asyncio.wait_for(process.wait(), timeout=12)
    except TimeoutError:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        await process.wait()
    await asyncio.sleep(0.25)
    try:
        os.killpg(process.pid, 0)
    except ProcessLookupError:
        pass
    else:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    if output_task is not None:
        try:
            await asyncio.wait_for(output_task, timeout=3)
        except TimeoutError:
            output_task.cancel()


def _existing_run_dirs() -> set[Path]:
    if not RUNS_DIR.exists():
        return set()
    return {path for path in RUNS_DIR.iterdir() if path.is_dir()}


async def _newest_run_dir(before: set[Path], timeout: float = 12) -> Path | None:
    deadline = time.monotonic() + timeout
    previous_signature: tuple[int, int] | None = None
    stable_observations = 0
    newest = None
    while time.monotonic() < deadline:
        new_dirs = _existing_run_dirs() - before
        if new_dirs:
            newest = max(new_dirs, key=lambda path: path.stat().st_mtime_ns)
            metrics_path = newest / "metrics.json"
            if metrics_path.exists():
                stat = metrics_path.stat()
                signature = (stat.st_size, stat.st_mtime_ns)
                if signature == previous_signature:
                    stable_observations += 1
                    if stable_observations >= 2:
                        return newest
                else:
                    stable_observations = 0
                    previous_signature = signature
        await asyncio.sleep(0.3)
    return newest


async def _wait_for_extraction(run_dir: Path, timeout: float = 90) -> str | None:
    deadline = time.monotonic() + timeout
    intake_path = run_dir / "intake.json"
    while time.monotonic() < deadline:
        if intake_path.exists():
            try:
                status = json.loads(intake_path.read_text(encoding="utf-8")).get(
                    "extraction_status"
                )
            except (OSError, json.JSONDecodeError):
                status = None
            if status and status != "pending":
                return status
        await asyncio.sleep(0.3)
    return None


def _create_results_dir() -> Path:
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    path = RESULTS_DIR / stamp
    suffix = 1
    while path.exists():
        path = RESULTS_DIR / f"{stamp}-{suffix}"
        suffix += 1
    path.mkdir(parents=True)
    return path


async def run_matrix(
    stt_providers: Sequence[str],
    tts_providers: Sequence[str],
    scenario_names: Sequence[str],
    repetitions: Sequence[int],
    speechify_voice: str | None = None,
    elevenlabs_voice: str | None = None,
    llm_providers: Sequence[str] = ("google",),
    smart_turn_stop_secs: float = SMART_TURN_STOP_DEFAULT_SECS,
    knowledge_digest_path: str | None = None,
    transport: str = "webrtc",
    fake_api: bool = False,
) -> Path:
    return await _run_matrix_with_options(
        stt_providers,
        tts_providers,
        scenario_names,
        repetitions,
        speechify_voice,
        elevenlabs_voice,
        llm_providers,
        smart_turn_stop_secs,
        knowledge_digest_path,
        transport=transport,
        fake_api=fake_api,
    )


async def _run_matrix_with_options(
    stt_providers: Sequence[str],
    tts_providers: Sequence[str],
    scenario_names: Sequence[str],
    repetitions: Sequence[int],
    speechify_voice: str | None,
    elevenlabs_voice: str | None,
    llm_providers: Sequence[str],
    smart_turn_stop_secs: float,
    knowledge_digest_path: str | None,
    *,
    transport: str,
    fake_api: bool,
) -> Path:
    if transport not in {"webrtc", "twilio"}:
        raise ValueError("transport must be 'webrtc' or 'twilio'")
    if fake_api:
        async with _running_fake_api(knowledge_digest_path=knowledge_digest_path) as api_base_url:
            return await _run_matrix_calls(
                stt_providers,
                tts_providers,
                scenario_names,
                repetitions,
                speechify_voice,
                elevenlabs_voice,
                llm_providers,
                smart_turn_stop_secs,
                knowledge_digest_path,
                transport=transport,
                api_base_url=api_base_url,
            )
    return await _run_matrix_calls(
        stt_providers,
        tts_providers,
        scenario_names,
        repetitions,
        speechify_voice,
        elevenlabs_voice,
        llm_providers,
        smart_turn_stop_secs,
        knowledge_digest_path,
        transport=transport,
        api_base_url=None,
    )


async def _run_matrix_calls(
    stt_providers: Sequence[str],
    tts_providers: Sequence[str],
    scenario_names: Sequence[str],
    repetitions: Sequence[int],
    speechify_voice: str | None,
    elevenlabs_voice: str | None,
    llm_providers: Sequence[str],
    smart_turn_stop_secs: float,
    knowledge_digest_path: str | None,
    *,
    transport: str,
    api_base_url: str | None,
) -> Path:
    scenarios = load_scenarios()
    unknown_scenarios = [name for name in scenario_names if name not in scenarios]
    if unknown_scenarios:
        raise ValueError(f"Unknown scenario(s): {', '.join(unknown_scenarios)}")
    if not repetitions or any(rep < 1 for rep in repetitions):
        raise ValueError("Each repetition count must be a positive integer.")
    if not math.isfinite(smart_turn_stop_secs) or smart_turn_stop_secs <= 0:
        raise ValueError("--smart-turn-stop-secs must be a positive number.")
    await synthesize_scenarios(list(dict.fromkeys(scenario_names)))

    results_dir = _create_results_dir()
    calls: list[dict[str, Any]] = []
    provider_errors: list[str] = []
    caller_errors: list[str] = []

    for stt in dict.fromkeys(stt_providers):
        for tts in dict.fromkeys(tts_providers):
            for llm in dict.fromkeys(llm_providers):
                process = None
                output_task = None
                output_lines = []
                active_caller_id = object()
                active_assistant = object()
                try:
                    for scenario_name in scenario_names:
                        caller_id = scenarios[scenario_name].get("caller_id")
                        assistant_path = scenarios[scenario_name].get("assistant")
                        if api_base_url is not None and assistant_path:
                            await _set_fake_assistant(api_base_url, assistant_path)
                        if (
                            process is None
                            or (transport == "webrtc" and caller_id != active_caller_id)
                            or (api_base_url is None and assistant_path != active_assistant)
                        ):
                            if process is not None:
                                await _stop_runner(process, output_task)
                            print(
                                f"\n=== Starting runner for STT={stt}, TTS={tts}, "
                                f"LLM={llm}, scenario={scenario_name} ==="
                            )
                            process, output_task, output_lines = await _start_runner(
                                stt,
                                tts,
                                llm,
                                speechify_voice,
                                elevenlabs_voice,
                                smart_turn_stop_secs,
                                knowledge_digest_path,
                                caller_id if transport == "webrtc" else None,
                                transport,
                                api_base_url,
                                FAKE_API_TOKEN if api_base_url is not None else None,
                                assistant_path,
                                str(results_dir / "twilio_call_control.jsonl")
                                if transport == "twilio"
                                else None,
                            )
                            active_caller_id = caller_id
                            active_assistant = assistant_path
                        for repetition in repetitions:
                            before = _existing_run_dirs()
                            scratch = (
                                results_dir
                                / "calls"
                                / f"{stt}-{tts}-{llm}-{scenario_name}-rep{repetition}"
                            )
                            state_before = (
                                await _fake_api_state(api_base_url)
                                if api_base_url is not None
                                else None
                            )
                            if transport == "twilio":
                                timeline = await run_twilio_call(
                                    scenario_name,
                                    scratch,
                                    base_url=PHONE_SERVER_URL,
                                    auth_token="test-auth-token",
                                    to_number=FAKE_API_TO_NUMBER,
                                    from_number=caller_id,
                                    audio_dir=AUDIO_DIR,
                                    fake_call_log_path=results_dir / "twilio_call_control.jsonl",
                                )
                            else:
                                timeline = await run_call(
                                    scenario_name,
                                    scratch,
                                    base_url=RUNNER_URL,
                                    audio_dir=AUDIO_DIR,
                                )
                            run_dir = await _newest_run_dir(before)
                            if run_dir is not None:
                                await _wait_for_extraction(run_dir)
                                metrics_path = run_dir / "metrics.json"
                                if metrics_path.exists():
                                    run_metrics = json.loads(
                                        metrics_path.read_text(encoding="utf-8")
                                    )
                                    timeline["number_holds"] = run_metrics.get("number_holds", [])
                                    timeline_path = scratch / "caller_timeline.json"
                                    timeline_path.write_text(
                                        json.dumps(timeline, indent=2, ensure_ascii=False) + "\n",
                                        encoding="utf-8",
                                    )
                            report_posted = None
                            external_call_id = None
                            if api_base_url is not None and state_before is not None:
                                report_posted, external_call_id = await _wait_for_fake_report(
                                    api_base_url,
                                    len(state_before["calls"]),
                                    timeout=5 if run_dir is not None else 1,
                                )
                            if run_dir is not None:
                                for filename in (
                                    "bot_audio.wav",
                                    "caller_audio.wav",
                                    "caller_timeline.json",
                                ):
                                    source = scratch / filename
                                    if source.exists():
                                        shutil.copy2(source, run_dir / filename)
                                scored = score_call(run_dir, scenarios[scenario_name])
                                scored["run_dir"] = str(run_dir)
                            else:
                                scored = score_call(scratch, scenarios[scenario_name])
                                scored["run_dir"] = None
                            scored.update(
                                {
                                    "stt": stt,
                                    "tts": tts,
                                    "llm": llm,
                                    "llm_model": (
                                        scored.get("llm_model") or LLM_DEFAULT_MODELS[llm]
                                    ),
                                    "scenario": scenario_name,
                                    "rep": repetition,
                                    "transport": transport,
                                    "report_posted": report_posted,
                                    "external_call_id": external_call_id,
                                    "smart_turn_stop_secs": smart_turn_stop_secs,
                                    "caller_error": timeline.get("error"),
                                    "caller_timeline_path": (
                                        str(run_dir / "caller_timeline.json")
                                        if run_dir is not None
                                        else str(scratch / "caller_timeline.json")
                                    ),
                                }
                            )
                            calls.append(scored)
                            if timeline.get("error"):
                                caller_errors.append(
                                    f"{stt}/{tts}/{scenario_name} rep {repetition}: "
                                    f"{timeline['error']}"
                                )
                            found_errors = [
                                line for line in output_lines if PROVIDER_ERROR.search(line)
                            ]
                            if found_errors:
                                provider_errors.extend(found_errors)
                                break
                        if provider_errors:
                            break
                finally:
                    if process is not None:
                        await _stop_runner(process, output_task)
                if provider_errors:
                    break
            if provider_errors:
                break
        if provider_errors:
            break

    results = {
        "created_at": datetime.now(UTC).isoformat(),
        "transport": transport,
        "fake_api": api_base_url is not None,
        "smart_turn_stop_secs": smart_turn_stop_secs,
        "calls": calls,
        "provider_errors": provider_errors,
        "caller_errors": caller_errors,
    }
    (results_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary = render_summary(calls)
    summary += f"\n\nTransport: {transport}\nFake API: {api_base_url is not None}\n"
    if api_base_url is not None:
        summary += (
            "\n| Scenario | Rep | Transport | Report posted | External call ID |\n"
            "| --- | ---: | --- | --- | --- |\n"
        )
        for call in calls:
            summary += (
                f"| {call.get('scenario', '—')} | {call.get('rep', '—')} | "
                f"{call.get('transport', '—')} | {call.get('report_posted')} | "
                f"{call.get('external_call_id') or '—'} |\n"
            )
    (results_dir / "summary.md").write_text(summary, encoding="utf-8")
    print(f"\nResults: {results_dir}")
    print(summary)
    if provider_errors:
        raise RuntimeError(
            f"Provider error detected; stopped without switching providers:\n{provider_errors[-1]}"
        )
    if caller_errors:
        raise RuntimeError(
            f"Scripted caller failed; inspect the saved artifacts:\n{caller_errors[-1]}"
        )
    return results_dir


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run scripted voice-provider A/B calls.")
    parser.add_argument("--stt", nargs="+", choices=STT_PROVIDERS, required=True)
    parser.add_argument("--tts", nargs="+", choices=TTS_PROVIDERS, required=True)
    parser.add_argument("--llm", nargs="+", choices=LLM_PROVIDERS, default=["google"])
    parser.add_argument("--scenario", nargs="+", required=True)
    parser.add_argument("--reps", nargs="+", type=int, required=True)
    parser.add_argument("--speechify-voice")
    parser.add_argument("--elevenlabs-voice")
    parser.add_argument(
        "--smart-turn-stop-secs",
        type=float,
        default=SMART_TURN_STOP_DEFAULT_SECS,
    )
    parser.add_argument("--knowledge-fixture", action="store_true")
    parser.add_argument("--transport", choices=("webrtc", "twilio"), default="webrtc")
    parser.add_argument("--fake-api", action="store_true")
    return parser


def main() -> None:
    parser = build_parser()
    arguments = parser.parse_args()
    if not arguments.knowledge_fixture:
        asyncio.run(
            run_matrix(
                arguments.stt,
                arguments.tts,
                arguments.scenario,
                arguments.reps,
                arguments.speechify_voice,
                arguments.elevenlabs_voice,
                arguments.llm,
                arguments.smart_turn_stop_secs,
                transport=arguments.transport,
                fake_api=arguments.fake_api,
            )
        )
        return

    from functools import partial
    from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
    from threading import Thread

    from inbound_voice.knowledge import import_knowledge

    fixture_site = VOICE_ROOT / "eval" / "fixtures" / "demo_site"
    fixture_docs = VOICE_ROOT / "eval" / "fixtures" / "demo_docs" / "client_care.md"
    server = ThreadingHTTPServer(
        ("127.0.0.1", 0),
        partial(SimpleHTTPRequestHandler, directory=str(fixture_site)),
    )
    server_thread = Thread(target=server.serve_forever, daemon=True)
    server_thread.start()
    try:
        with tempfile.TemporaryDirectory(prefix="inbound-knowledge-") as temporary:
            imported = asyncio.run(
                import_knowledge(
                    assistant_path=VOICE_ROOT / "config" / "assistants" / "demo_firm.yaml",
                    urls=[f"http://127.0.0.1:{server.server_port}/"],
                    files=[fixture_docs],
                    output_dir=Path(temporary) / "knowledge" / "demo_firm",
                )
            )
            print(
                f"Knowledge fixture digest: {imported['digest_chars']} chars; "
                f"cost {imported['digest_cost']}"
            )
            results_dir = asyncio.run(
                run_matrix(
                    arguments.stt,
                    arguments.tts,
                    arguments.scenario,
                    arguments.reps,
                    arguments.speechify_voice,
                    arguments.elevenlabs_voice,
                    arguments.llm,
                    arguments.smart_turn_stop_secs,
                    imported["digest_path"],
                    transport=arguments.transport,
                    fake_api=arguments.fake_api,
                )
            )
            digest_copy = results_dir / "knowledge_fixture" / "digest.md"
            digest_copy.parent.mkdir(parents=True, exist_ok=True)
            digest_bytes = Path(imported["digest_path"]).read_bytes()
            digest_copy.write_bytes(digest_bytes)
            results_path = results_dir / "results.json"
            result_data = json.loads(results_path.read_text(encoding="utf-8"))
            result_data["knowledge_fixture"] = {
                "digest_path": str(digest_copy),
                "digest_sha256": hashlib.sha256(digest_bytes).hexdigest(),
                "digest_chars": imported["digest_chars"],
                "digest_cost": imported["digest_cost"],
            }
            results_path.write_text(
                json.dumps(result_data, indent=2, ensure_ascii=False) + "\n",
                encoding="utf-8",
            )
            print(
                f"Knowledge digest saved at {digest_copy} "
                f"(SHA-256 {result_data['knowledge_fixture']['digest_sha256']})"
            )
    finally:
        server.shutdown()
        server.server_close()
        server_thread.join(timeout=2)


if __name__ == "__main__":
    main()
