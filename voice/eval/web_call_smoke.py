from __future__ import annotations

import argparse
import asyncio
import os
import subprocess
import threading
import time
from pathlib import Path
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import aiohttp
from websockets.sync.client import connect

from eval.synth_callers import _synthesize_utterance

VOICE_ROOT = Path(__file__).resolve().parents[1]
SAMPLE_RATE = 16000
CHUNK_SAMPLES = 320
DEFAULT_UTTERANCE = "Hi, I'd like to know your remortgage fee."


async def synthesize_pcm(text: str, voice: str) -> bytes:
    api_key = os.environ.get("SPEECHIFY_API_KEY", "").strip()
    if not api_key:
        raise RuntimeError("SPEECHIFY_API_KEY is required to synthesize the caller utterance.")
    async with aiohttp.ClientSession() as session:
        mp3_audio = await _synthesize_utterance(session, api_key, text, voice)
    result = subprocess.run(
        [
            "ffmpeg",
            "-hide_banner",
            "-loglevel",
            "error",
            "-i",
            "pipe:0",
            "-ac",
            "1",
            "-ar",
            str(SAMPLE_RATE),
            "-f",
            "s16le",
            "pipe:1",
        ],
        input=mp3_audio,
        capture_output=True,
        check=False,
    )
    if result.returncode:
        detail = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not convert synthesized caller audio: {detail}")
    if not result.stdout or len(result.stdout) % 2:
        raise RuntimeError("ffmpeg did not produce valid 16-bit caller PCM.")
    return result.stdout


def receive_audio_until_quiet(
    websocket,
    *,
    timeout: float,
    quiet_seconds: float = 1.2,
) -> tuple[int, float | None]:
    started_at = time.monotonic()
    last_audio_at = None
    first_audio_at = None
    total_bytes = 0
    while time.monotonic() - started_at < timeout:
        try:
            message = websocket.recv(timeout=min(0.25, timeout - (time.monotonic() - started_at)))
        except TimeoutError:
            if last_audio_at is not None and time.monotonic() - last_audio_at >= quiet_seconds:
                return total_bytes, first_audio_at
            continue
        if isinstance(message, bytes):
            now = time.monotonic()
            first_audio_at = first_audio_at or now
            last_audio_at = now
            total_bytes += len(message)
    raise TimeoutError(f"No quiet end-of-audio gap arrived within {timeout:.0f} seconds.")


def _stream_silence_realtime(websocket, stop: threading.Event) -> None:
    chunk_bytes = CHUNK_SAMPLES * 2
    for _ in range(60):
        if stop.is_set():
            break
        websocket.send(bytes(chunk_bytes))
        if stop.wait(CHUNK_SAMPLES / SAMPLE_RATE):
            break


def stream_pcm_realtime(websocket, pcm: bytes) -> tuple[float, threading.Event, threading.Thread]:
    chunk_bytes = CHUNK_SAMPLES * 2
    for offset in range(0, len(pcm), chunk_bytes):
        chunk = pcm[offset : offset + chunk_bytes]
        websocket.send(chunk)
        time.sleep(len(chunk) / (SAMPLE_RATE * 2))
    speech_finished_at = time.monotonic()
    stop = threading.Event()
    silence_sender = threading.Thread(
        target=_stream_silence_realtime,
        args=(websocket, stop),
        daemon=True,
    )
    silence_sender.start()
    return speech_finished_at, stop, silence_sender


def _call_run_dirs() -> set[str]:
    runs = VOICE_ROOT / "runs"
    return {path.name for path in runs.iterdir() if path.is_dir()} if runs.exists() else set()


def _wait_for_transcript(previous_runs: set[str], timeout: float = 30) -> Path:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for run_dir in (VOICE_ROOT / "runs").iterdir():
            transcript = run_dir / "transcript.jsonl"
            if run_dir.is_dir() and run_dir.name not in previous_runs and transcript.is_file():
                return transcript
        time.sleep(0.2)
    raise TimeoutError("No new call transcript appeared under voice/runs.")


def run_smoke(
    url: str,
    text: str,
    voice: str,
    token: str | None,
    timeout: float,
) -> tuple[int, int, float, Path]:
    pcm = asyncio.run(synthesize_pcm(text, voice))
    previous_runs = _call_run_dirs()
    parts = urlsplit(url)
    query = [(key, value) for key, value in parse_qsl(parts.query) if key != "token"]
    if token:
        query.append(("token", token))
    ws_url = urlunsplit(parts._replace(query=urlencode(query)))

    with connect(ws_url, open_timeout=10, close_timeout=5) as websocket:
        greeting_bytes, _ = receive_audio_until_quiet(websocket, timeout=timeout)
        if greeting_bytes == 0:
            raise RuntimeError("The server did not send greeting audio.")
        utterance_finished_at, stop_silence, silence_sender = stream_pcm_realtime(
            websocket,
            pcm,
        )
        try:
            reply_bytes, first_reply_at = receive_audio_until_quiet(websocket, timeout=timeout)
        finally:
            stop_silence.set()
            silence_sender.join(timeout=2)
        if first_reply_at is None or reply_bytes == 0:
            raise RuntimeError("The server did not send reply audio.")
        time_to_reply = first_reply_at - utterance_finished_at

    transcript = _wait_for_transcript(previous_runs)
    return greeting_bytes, reply_bytes, time_to_reply, transcript


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Smoke-test the hosted browser-call WebSocket.")
    parser.add_argument("--url", default="ws://127.0.0.1:7862/ws")
    parser.add_argument("--token", default=os.environ.get("WEB_CALL_TOKEN"))
    parser.add_argument("--text", default=DEFAULT_UTTERANCE)
    parser.add_argument("--voice", default="hamish")
    parser.add_argument("--timeout", type=float, default=60)
    return parser


def main() -> None:
    args = build_parser().parse_args()
    greeting_bytes, reply_bytes, first_reply_seconds, transcript = run_smoke(
        args.url,
        args.text,
        args.voice,
        args.token,
        args.timeout,
    )
    print(f"Greeting audio bytes: {greeting_bytes}")
    print(f"Reply audio bytes: {reply_bytes}")
    print(f"Time to first reply audio: {first_reply_seconds:.2f}s")
    print(f"Transcript: {transcript}")


if __name__ == "__main__":
    main()
