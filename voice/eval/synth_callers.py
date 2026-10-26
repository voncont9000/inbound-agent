from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import io
import math
import os
import re
import shutil
import struct
import subprocess
import time
import wave
from collections.abc import Mapping
from datetime import UTC, datetime
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any

import aiohttp
import numpy as np
import soxr
import yaml

VOICE_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")
AUDIO_DIR = Path(__file__).with_name("audio")
SPEECHIFY_URL = "https://api.speechify.ai/v1/audio/speech"
WEBRTC_SAMPLE_RATE = 48_000
PAUSE_MARKER = re.compile(r"\s*\|\s*pause\s+(\d+(?:\.\d+)?)\s*\|\s*", re.IGNORECASE)
SPEECHIFY_REQUEST_INTERVAL_SECONDS = 1.1
SPEECHIFY_RETRY_BACKOFFS = (2, 4, 8, 16)
_speechify_request_lock = asyncio.Lock()
_last_speechify_request_started: float | None = None


def load_scenarios(path: str | Path = SCENARIOS_PATH) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", {}) if isinstance(data, dict) else {}
    if not isinstance(scenarios, dict):
        raise ValueError("scenarios.yaml must contain a scenarios mapping.")
    return scenarios


def split_paused_utterance(utterance: str) -> tuple[list[str], list[float]]:
    parts = []
    pauses = []
    cursor = 0
    for marker in PAUSE_MARKER.finditer(utterance):
        part = utterance[cursor : marker.start()].strip()
        pause = float(marker.group(1))
        if not part or not math.isfinite(pause) or pause <= 0:
            raise ValueError("Pause markers require non-empty speech and a positive duration.")
        parts.append(part)
        pauses.append(pause)
        cursor = marker.end()
    final_part = utterance[cursor:].strip()
    if not final_part:
        raise ValueError("Pause markers must be followed by caller speech.")
    parts.append(final_part)
    return parts, pauses


def utterance_audio_path(
    audio_dir: str | Path,
    scenario_name: str,
    index: int,
    utterance: str,
    caller_voice: str = "",
) -> Path:
    _, pauses = split_paused_utterance(utterance)
    suffix = "-paused" if pauses else ""
    digest = hashlib.sha256(f"{caller_voice}\0{utterance}".encode()).hexdigest()[:12]
    return Path(audio_dir) / scenario_name / f"{index}{suffix}-{digest}.wav"


def combine_segment_wavs(segment_wavs: list[bytes], pauses: list[float]) -> bytes:
    if len(segment_wavs) != len(pauses) + 1:
        raise ValueError("Each pause must separate two synthesized speech segments.")
    segments = []
    for segment_wav in segment_wavs:
        with wave.open(io.BytesIO(segment_wav), "rb") as audio_file:
            if (
                audio_file.getnchannels() != 1
                or audio_file.getsampwidth() != 2
                or audio_file.getframerate() != WEBRTC_SAMPLE_RATE
            ):
                raise ValueError("Caller segments must be 48 kHz, mono, 16-bit WAV files.")
            segments.append(
                np.frombuffer(audio_file.readframes(audio_file.getnframes()), dtype=np.int16)
            )

    chunks = []
    for index, segment in enumerate(segments):
        chunks.append(segment)
        if index < len(pauses):
            chunks.append(np.zeros(round(pauses[index] * WEBRTC_SAMPLE_RATE), dtype=np.int16))
    samples = np.concatenate(chunks)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(WEBRTC_SAMPLE_RATE)
        audio_file.writeframes(samples.tobytes())
    return output.getvalue()


def phone_band_wav(mp3_audio: bytes) -> bytes:
    command = [
        "ffmpeg",
        "-hide_banner",
        "-loglevel",
        "error",
        "-i",
        "pipe:0",
        "-af",
        "aresample=8000,aresample=48000",
        "-ac",
        "1",
        "-ar",
        str(WEBRTC_SAMPLE_RATE),
        "-sample_fmt",
        "s16",
        "-f",
        "wav",
        "pipe:1",
    ]
    try:
        result = subprocess.run(
            command,
            input=mp3_audio,
            capture_output=True,
            check=False,
        )
    except FileNotFoundError as error:
        raise RuntimeError("ffmpeg is required to prepare 8 kHz caller audio.") from error
    if result.returncode:
        details = result.stderr.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"Could not convert Speechify audio to phone-band WAV: {details}")
    if not result.stdout.startswith(b"RIFF"):
        raise RuntimeError("ffmpeg did not produce a WAV file.")
    return result.stdout


def phone_band_from_wav(wav_bytes: bytes) -> bytes:
    if len(wav_bytes) < 12 or wav_bytes[:4] != b"RIFF" or wav_bytes[8:12] != b"WAVE":
        raise ValueError("Speechify did not return a RIFF/WAVE file.")

    format_chunk = None
    data_chunk = None
    position = 12
    while position + 8 <= len(wav_bytes):
        chunk_id = wav_bytes[position : position + 4]
        chunk_size = struct.unpack_from("<I", wav_bytes, position + 4)[0]
        chunk_start = position + 8
        chunk_end = min(chunk_start + chunk_size, len(wav_bytes))
        chunk_data = wav_bytes[chunk_start:chunk_end]
        if chunk_id == b"fmt ":
            format_chunk = chunk_data
        elif chunk_id == b"data":
            data_chunk = chunk_data
            if chunk_size > len(wav_bytes) - chunk_start:
                data_chunk = wav_bytes[chunk_start:]
                break
        if chunk_end == len(wav_bytes):
            break
        position = chunk_start + chunk_size + (chunk_size & 1)

    if format_chunk is None or len(format_chunk) < 16 or data_chunk is None:
        raise ValueError("Speechify WAV must contain fmt and data chunks.")
    encoding, channels, sample_rate, _, _, sample_width = struct.unpack_from(
        "<HHIIHH", format_chunk
    )
    if encoding != 1 or sample_width != 16 or channels < 1 or sample_rate < 1:
        raise ValueError("Speechify WAV must use 16-bit PCM audio.")
    frame_width = channels * 2
    frame_count = len(data_chunk) // frame_width
    if frame_count == 0:
        raise ValueError("Speechify WAV contains no complete PCM frames.")
    samples = np.frombuffer(data_chunk[: frame_count * frame_width], dtype="<i2")
    if channels > 1:
        samples = np.rint(samples.reshape(-1, channels).astype(np.float64).mean(axis=1))
        samples = samples.clip(-32768, 32767).astype(np.int16)
    else:
        samples = samples.astype(np.int16, copy=False)
    at_8khz = soxr.resample(samples.astype(np.float32), sample_rate, 8000)
    at_48khz = soxr.resample(at_8khz, 8000, WEBRTC_SAMPLE_RATE)
    output = io.BytesIO()
    with wave.open(output, "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(WEBRTC_SAMPLE_RATE)
        audio_file.writeframes(np.rint(at_48khz).clip(-32768, 32767).astype(np.int16).tobytes())
    return output.getvalue()


async def _wait_for_speechify_slot() -> None:
    global _last_speechify_request_started

    async with _speechify_request_lock:
        now = time.monotonic()
        if _last_speechify_request_started is not None:
            delay = SPEECHIFY_REQUEST_INTERVAL_SECONDS - (now - _last_speechify_request_started)
            if delay > 0:
                await asyncio.sleep(delay)
        _last_speechify_request_started = time.monotonic()


def _retry_delay(retry_after: str | None, fallback: float) -> float:
    if not retry_after:
        return fallback
    try:
        delay = float(retry_after)
    except ValueError:
        try:
            retry_at = parsedate_to_datetime(retry_after)
        except (TypeError, ValueError, OverflowError):
            return fallback
        if retry_at.tzinfo is None:
            retry_at = retry_at.replace(tzinfo=UTC)
        return max(0.0, (retry_at - datetime.now(UTC)).total_seconds())
    return max(0.0, delay) if math.isfinite(delay) else fallback


async def _synthesize_utterance(
    session: aiohttp.ClientSession,
    api_key: str,
    text: str,
    caller_voice: str,
    audio_format: str = "mp3",
) -> bytes:
    payload: Any = None
    for retry_number in range(len(SPEECHIFY_RETRY_BACKOFFS) + 1):
        await _wait_for_speechify_slot()
        async with session.post(
            SPEECHIFY_URL,
            headers={"Authorization": f"Bearer {api_key}"},
            json={
                "input": text,
                "voice_id": caller_voice,
                "model": "simba-3.2",
                "audio_format": audio_format,
            },
        ) as response:
            if response.status == 429:
                body = await response.text()
                if retry_number == len(SPEECHIFY_RETRY_BACKOFFS):
                    raise RuntimeError(f"Speechify synthesis failed ({response.status}): {body}")
                retry_after = response.headers.get("Retry-After")
                delay = _retry_delay(
                    retry_after,
                    SPEECHIFY_RETRY_BACKOFFS[retry_number],
                )
            elif response.status >= 400:
                body = await response.text()
                raise RuntimeError(f"Speechify synthesis failed ({response.status}): {body}")
            else:
                payload = await response.json()
                break
        await asyncio.sleep(delay)
    try:
        audio = base64.b64decode(payload["audio_data"], validate=True)
    except (KeyError, ValueError, TypeError) as error:
        raise RuntimeError("Speechify response did not contain valid base64 audio_data.") from error
    if not audio:
        raise RuntimeError("Speechify returned empty audio.")
    return audio


async def synthesize_scenarios(
    scenario_names: list[str],
    output_dir: str | Path = AUDIO_DIR,
    env: Mapping[str, str] | None = None,
    scenarios_path: str | Path = SCENARIOS_PATH,
) -> dict[str, list[Path]]:
    environment = os.environ if env is None else env
    scenarios = load_scenarios(scenarios_path)
    missing = [name for name in scenario_names if name not in scenarios]
    if missing:
        raise ValueError(f"Unknown scenario(s): {', '.join(missing)}")

    root = Path(output_dir)
    targets: dict[str, list[Path]] = {}
    pending: list[tuple[str, int, list[str], list[float], str, Path]] = []
    for name in scenario_names:
        scenario = scenarios[name]
        caller_voice = scenario["caller_voice"]
        targets[name] = []
        for index, utterance in enumerate(scenario["utterances"]):
            path = utterance_audio_path(root, name, index, utterance, caller_voice)
            targets[name].append(path)
            if not path.exists():
                parts, pauses = split_paused_utterance(utterance)
                pending.append((name, index, parts, pauses, caller_voice, path))
    if not pending:
        return targets

    ffmpeg_available = shutil.which("ffmpeg") is not None
    api_key = environment.get("SPEECHIFY_API_KEY", "").strip()
    if not api_key:
        raise ValueError("SPEECHIFY_API_KEY is required to synthesize uncached caller audio.")

    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _, _, parts, pauses, caller_voice, path in pending:
            segment_wavs = []
            for part in parts:
                audio = await _synthesize_utterance(
                    session,
                    api_key,
                    part,
                    caller_voice,
                    audio_format="mp3" if ffmpeg_available else "wav",
                )
                segment_wavs.append(
                    phone_band_wav(audio) if ffmpeg_available else phone_band_from_wav(audio)
                )
            converted = combine_segment_wavs(segment_wavs, pauses)
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(converted)
    return targets


async def _main(scenario_names: list[str]) -> None:
    outputs = await synthesize_scenarios(scenario_names)
    for scenario, files in outputs.items():
        print(f"{scenario}: {len(files)} utterance WAV files ready")


def main() -> None:
    parser = argparse.ArgumentParser(description="Synthesize cached scripted caller audio.")
    parser.add_argument("--scenario", nargs="+", required=True)
    arguments = parser.parse_args()
    asyncio.run(_main(arguments.scenario))


if __name__ == "__main__":
    main()
