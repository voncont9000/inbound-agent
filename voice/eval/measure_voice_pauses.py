"""Measure mid-sentence pauses per Speechify voice.

Synthesises punctuation-free sentences with each voice at its production rate
(pcm_8000, as phone calls use) and counts internal silences of at least 250 ms
below -50 dBFS. Results feed ``pauses_per_10s`` in
core/inbound_core/data/speechify_voice_pace.json. Speechify limits concurrency,
so requests run sequentially. Run where SPEECHIFY_API_KEY is set, e.g. inside
the prod phone container:

    python -m eval.measure_voice_pauses [voice_id ...] > pauses.json
"""

from __future__ import annotations

import array
import asyncio
import base64
import json
import math
import os
import sys

import aiohttp
from inbound_core.voice_pace import _speechify_voice_pace, speechify_rate_percent

URL = "https://api.speechify.ai/v1/audio/stream/with-timestamps"
SAMPLE_RATE = 8000
WINDOW_S = 0.02
SILENCE_DBFS = -50
MIN_PAUSE_MS = 250
SENTENCES = [
    "Are you still looking to discuss that listing in Notting Hill with the team",
    "Our listings and services cover Greater London as well as the surrounding areas",
    "I can ask someone from the office to give you a call back tomorrow morning",
    "Thank you for calling and I hope you have a lovely rest of your day",
    "The team will be in touch as soon as the office opens again on Monday",
    "Could you tell me which date and time would work best for your viewing",
]


def internal_pauses_ms(pcm: bytes, sample_rate: int = SAMPLE_RATE) -> list[int]:
    samples = array.array("h", pcm)
    window = int(sample_rate * WINDOW_S)
    pauses: list[int] = []
    silent_windows = 0
    voiced = False
    for start in range(0, len(samples) - window, window):
        chunk = samples[start : start + window]
        rms = math.sqrt(sum(value * value for value in chunk) / len(chunk))
        dbfs = 20 * math.log10(rms / 32768) if rms > 0 else -120
        if dbfs > SILENCE_DBFS:
            pause_ms = round(silent_windows * WINDOW_S * 1000)
            if voiced and pause_ms >= MIN_PAUSE_MS:
                pauses.append(pause_ms)
            silent_windows = 0
            voiced = True
        else:
            silent_windows += 1
    return pauses


async def synthesise(session: aiohttp.ClientSession, voice_id: str, text: str) -> bytes | None:
    rate = speechify_rate_percent(voice_id, 1.0)
    payload_input = f'<speak><prosody rate="{rate:+d}%">{text}</prosody></speak>' if rate else text
    body = {
        "input": payload_input,
        "voice_id": voice_id,
        "model": "simba-3.2",
        "output_format": f"pcm_{SAMPLE_RATE}",
    }
    headers = {"Authorization": f"Bearer {os.environ['SPEECHIFY_API_KEY']}"}
    for attempt in range(6):
        await asyncio.sleep(0.8)
        async with session.post(URL, json=body, headers=headers) as response:
            if response.status != 200:
                await asyncio.sleep(4 * (attempt + 1))
                continue
            pcm = b""
            event = None
            async for raw in response.content:
                line = raw.decode().strip()
                if line.startswith("event:"):
                    event = line[6:].strip()
                elif line.startswith("data:") and event == "speech.chunk":
                    audio = json.loads(line[5:]).get("audio")
                    if audio:
                        pcm += base64.b64decode(audio)
            return pcm
    return None


async def main(voice_ids: list[str]) -> dict[str, dict[str, float | int]]:
    results: dict[str, dict[str, float | int]] = {}
    async with aiohttp.ClientSession() as session:
        for voice_id in voice_ids:
            pauses: list[int] = []
            audio_s = 0.0
            for sentence in SENTENCES:
                pcm = await synthesise(session, voice_id, sentence)
                if pcm is None:
                    continue
                pauses += internal_pauses_ms(pcm)
                audio_s += len(pcm) / (2 * SAMPLE_RATE)
            if audio_s == 0:
                print(f"{voice_id}: synthesis failed", file=sys.stderr)
                continue
            results[voice_id] = {
                "audio_s": round(audio_s, 2),
                "pauses": len(pauses),
                "per_10s": round(len(pauses) / audio_s * 10, 2),
                "max_ms": max(pauses, default=0),
            }
            print(voice_id, results[voice_id], file=sys.stderr, flush=True)
    return results


if __name__ == "__main__":
    voices = sys.argv[1:] or sorted(_speechify_voice_pace()[1])
    print(json.dumps(asyncio.run(main(voices)), indent=2))
