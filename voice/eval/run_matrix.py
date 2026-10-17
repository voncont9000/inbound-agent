from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
import shutil
import signal
import time
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import aiohttp

from eval.caller import run_call
from eval.score import render_summary, score_call
from eval.synth_callers import AUDIO_DIR, synthesize_scenarios
from inbound_voice.providers import STT_PROVIDERS, TTS_PROVIDERS

VOICE_ROOT = Path(__file__).resolve().parents[1]
RUNS_DIR = VOICE_ROOT / "runs"
RESULTS_DIR = Path(__file__).with_name("results")
RUNNER_URL = "http://127.0.0.1:7860"
API_KEY_NAMES = (
    "OPENROUTER_API_KEY",
    "ASSEMBLYAI_API_KEY",
    "SONIOX_API_KEY",
    "SPEECHIFY_API_KEY",
    "ELEVENLABS_API_KEY",
    "DEEPGRAM_API_KEY",
)
PROVIDER_ERROR = re.compile(
    r"\b(401|403)\b|unauthorized|forbidden|authenticationerror|invalid[_ ]api[_ ]key|"
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


async def _start_runner(
    stt: str,
    tts: str,
) -> tuple[asyncio.subprocess.Process, asyncio.Task[None], list[str]]:
    process_env = os.environ.copy()
    process_env["STT_PROVIDER"] = stt
    process_env["TTS_PROVIDER"] = tts
    if tts == "speechify":
        process_env.pop("TTS_VOICE", None)
    process = await asyncio.create_subprocess_exec(
        "uv",
        "run",
        "bot.py",
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
        await _wait_for_runner(process, lines)
    except Exception:
        await _stop_runner(process, output_task)
        raise
    return process, output_task, lines


async def _wait_for_runner(
    process: asyncio.subprocess.Process,
    lines: list[str],
    timeout: float = 45,
) -> None:
    deadline = time.monotonic() + timeout
    async with aiohttp.ClientSession(timeout=aiohttp.ClientTimeout(total=2)) as session:
        while time.monotonic() < deadline:
            if process.returncode is not None:
                break
            try:
                async with session.get(f"{RUNNER_URL}/client/") as response:
                    if response.status == 200:
                        return
            except (aiohttp.ClientError, TimeoutError):
                pass
            await asyncio.sleep(0.25)
    tail = "\n".join(lines[-30:])
    if process.returncode is None:
        raise TimeoutError(f"Runner did not serve /client/ within {timeout}s.\n{tail}")
    raise RuntimeError(f"Runner exited with status {process.returncode}.\n{tail}")


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
) -> Path:
    from eval.synth_callers import load_scenarios

    scenarios = load_scenarios()
    unknown_scenarios = [name for name in scenario_names if name not in scenarios]
    if unknown_scenarios:
        raise ValueError(f"Unknown scenario(s): {', '.join(unknown_scenarios)}")
    if not repetitions or any(rep < 1 for rep in repetitions):
        raise ValueError("Each repetition count must be a positive integer.")
    await synthesize_scenarios(list(dict.fromkeys(scenario_names)))

    results_dir = _create_results_dir()
    calls: list[dict[str, Any]] = []
    provider_errors: list[str] = []
    caller_errors: list[str] = []

    for stt in dict.fromkeys(stt_providers):
        for tts in dict.fromkeys(tts_providers):
            print(f"\n=== Starting runner for STT={stt}, TTS={tts} ===")
            process, output_task, output_lines = await _start_runner(stt, tts)
            try:
                for scenario_name in scenario_names:
                    for repetition in repetitions:
                        before = _existing_run_dirs()
                        scratch = (
                            results_dir / "calls" / f"{stt}-{tts}-{scenario_name}-rep{repetition}"
                        )
                        timeline = await run_call(
                            scenario_name,
                            scratch,
                            base_url=RUNNER_URL,
                            audio_dir=AUDIO_DIR,
                        )
                        run_dir = await _newest_run_dir(before)
                        if run_dir is not None:
                            for filename in ("bot_audio.wav", "caller_timeline.json"):
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
                                "scenario": scenario_name,
                                "rep": repetition,
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
                                f"{stt}/{tts}/{scenario_name} rep {repetition}: {timeline['error']}"
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
                await _stop_runner(process, output_task)
            if provider_errors:
                break
        if provider_errors:
            break

    results = {
        "created_at": datetime.now(UTC).isoformat(),
        "calls": calls,
        "provider_errors": provider_errors,
        "caller_errors": caller_errors,
    }
    (results_dir / "results.json").write_text(
        json.dumps(results, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    summary = render_summary(calls)
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


def main() -> None:
    parser = argparse.ArgumentParser(description="Run scripted voice-provider A/B calls.")
    parser.add_argument("--stt", nargs="+", choices=STT_PROVIDERS, required=True)
    parser.add_argument("--tts", nargs="+", choices=TTS_PROVIDERS, required=True)
    parser.add_argument("--scenario", nargs="+", required=True)
    parser.add_argument("--reps", nargs="+", type=int, required=True)
    arguments = parser.parse_args()
    asyncio.run(
        run_matrix(
            arguments.stt,
            arguments.tts,
            arguments.scenario,
            arguments.reps,
        )
    )


if __name__ == "__main__":
    main()
