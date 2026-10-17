from __future__ import annotations

import argparse
import asyncio
import base64
import os
import subprocess
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import aiohttp
import yaml

VOICE_ROOT = Path(__file__).resolve().parents[1]
SCENARIOS_PATH = Path(__file__).with_name("scenarios.yaml")
AUDIO_DIR = Path(__file__).with_name("audio")
SPEECHIFY_URL = "https://api.speechify.ai/v1/audio/speech"
WEBRTC_SAMPLE_RATE = 48_000


def load_scenarios(path: str | Path = SCENARIOS_PATH) -> dict[str, Any]:
    data = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    scenarios = data.get("scenarios", {}) if isinstance(data, dict) else {}
    if not isinstance(scenarios, dict):
        raise ValueError("scenarios.yaml must contain a scenarios mapping.")
    return scenarios


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


async def _synthesize_utterance(
    session: aiohttp.ClientSession,
    api_key: str,
    text: str,
    caller_voice: str,
) -> bytes:
    async with session.post(
        SPEECHIFY_URL,
        headers={"Authorization": f"Bearer {api_key}"},
        json={
            "input": text,
            "voice_id": caller_voice,
            "model": "simba-3.2",
            "audio_format": "mp3",
        },
    ) as response:
        if response.status >= 400:
            body = await response.text()
            raise RuntimeError(f"Speechify synthesis failed ({response.status}): {body}")
        payload = await response.json()
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
    pending: list[tuple[str, int, str, str, Path]] = []
    for name in scenario_names:
        scenario = scenarios[name]
        caller_voice = scenario["caller_voice"]
        targets[name] = []
        for index, utterance in enumerate(scenario["utterances"]):
            path = root / name / f"{index}.wav"
            targets[name].append(path)
            if not path.exists():
                pending.append((name, index, utterance, caller_voice, path))
    if not pending:
        return targets

    api_key = environment.get("SPEECHIFY_API_KEY", "").strip()
    if not api_key:
        raise ValueError("SPEECHIFY_API_KEY is required to synthesize uncached caller audio.")

    timeout = aiohttp.ClientTimeout(total=90)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        for _, _, utterance, caller_voice, path in pending:
            audio = await _synthesize_utterance(session, api_key, utterance, caller_voice)
            converted = phone_band_wav(audio)
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
