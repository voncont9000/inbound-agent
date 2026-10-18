from __future__ import annotations

import argparse
import asyncio
import base64
import json
import time
import uuid
import wave
import xml.etree.ElementTree as ET
from collections import deque
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import soxr
from aiohttp import ClientSession, ClientTimeout, WSMsgType

from eval.caller import (
    AgentAudioRecorder,
    _create_timeline,
    _drive_conversation,
    _seconds_from_last_agent_speech_end_to_disconnect,
    _speech_segment_offsets,
)
from eval.synth_callers import AUDIO_DIR, SCENARIOS_PATH, WEBRTC_SAMPLE_RATE, load_scenarios
from inbound_voice.twilio import compute_signature

PHONE_SAMPLE_RATE = 8000
PHONE_FRAME_SAMPLES = PHONE_SAMPLE_RATE // 50
PHONE_FRAME_BYTES = PHONE_FRAME_SAMPLES
TWILIO_SILENCE_FRAME = b"\xff" * PHONE_FRAME_BYTES


def pcm16_to_mulaw(samples: np.ndarray) -> bytes:
    pcm = np.asarray(samples, dtype=np.int16).reshape(-1).astype(np.int32)
    clipped = np.clip(pcm, -32635, 32635)
    sign = np.where(clipped < 0, 0x80, 0)
    magnitude = np.abs(clipped) + 0x84
    exponent = np.zeros_like(magnitude)
    for bit in (0x4000, 0x2000, 0x1000, 0x0800, 0x0400, 0x0200, 0x0100, 0x0080):
        exponent += (magnitude >= bit).astype(np.int32)
    exponent = np.clip(exponent - 1, 0, 7)
    mantissa = (magnitude >> (exponent + 3)) & 0x0F
    encoded = ~(sign | (exponent << 4) | mantissa) & 0xFF
    return encoded.astype(np.uint8).tobytes()


def mulaw_to_pcm16(payload: bytes) -> np.ndarray:
    encoded = np.frombuffer(payload, dtype=np.uint8).astype(np.int32)
    value = ~encoded & 0xFF
    sign = value & 0x80
    exponent = (value >> 4) & 0x07
    mantissa = value & 0x0F
    magnitude = ((mantissa << 3) + 0x84) << exponent
    pcm = magnitude - 0x84
    pcm = np.where(sign != 0, -pcm, pcm)
    return pcm.astype(np.int16)


def frame_mulaw(payload: bytes, frame_bytes: int = PHONE_FRAME_BYTES) -> list[bytes]:
    if frame_bytes <= 0:
        raise ValueError("frame_bytes must be positive")
    return [
        payload[start : start + frame_bytes].ljust(frame_bytes, b"\xff")
        for start in range(0, len(payload), frame_bytes)
    ]


@dataclass
class _QueuedUtterance:
    frames: deque[bytes]
    timeline_entry: dict[str, Any]
    completed: asyncio.Event
    segments: list[tuple[float, float]]


class _CallerAudioQueue:
    def __init__(self, call_started: float) -> None:
        self.call_started = call_started
        self.queued: deque[_QueuedUtterance] = deque()
        self.current: _QueuedUtterance | None = None

    def queue_utterance(self, samples: np.ndarray, timeline_entry: dict[str, Any]) -> asyncio.Event:
        normalized = np.asarray(samples, dtype=np.int16).reshape(-1)
        segments: list[tuple[float, float]] = []
        if "| pause " in str(timeline_entry.get("text", "")):
            segments = [
                (start / WEBRTC_SAMPLE_RATE, end / WEBRTC_SAMPLE_RATE)
                for start, end in _speech_segment_offsets(normalized)
            ]
            timeline_entry["segments"] = [
                {
                    "start_offset_seconds": start,
                    "end_offset_seconds": end,
                    "start_seconds": None,
                    "end_seconds": None,
                }
                for start, end in segments
            ]
        resampled = soxr.resample(
            normalized.astype(np.float32), WEBRTC_SAMPLE_RATE, PHONE_SAMPLE_RATE
        )
        phone_pcm = np.rint(resampled).clip(-32768, 32767).astype(np.int16)
        frames = deque(frame_mulaw(pcm16_to_mulaw(phone_pcm)))
        completed = asyncio.Event()
        self.queued.append(_QueuedUtterance(frames, timeline_entry, completed, segments))
        return completed

    def next_frame(self, now: float) -> bytes:
        if self.current is None and self.queued:
            self.current = self.queued.popleft()
        item = self.current
        if item is None:
            return TWILIO_SILENCE_FRAME
        if item.timeline_entry["start_seconds"] is None:
            start_seconds = max(0.0, now - self.call_started)
            item.timeline_entry["start_seconds"] = start_seconds
            for segment, (start_offset, end_offset) in zip(
                item.timeline_entry.get("segments", []), item.segments, strict=True
            ):
                segment["start_seconds"] = start_seconds + start_offset
                segment["end_seconds"] = start_seconds + end_offset
        payload = item.frames.popleft()
        if not item.frames:
            item.timeline_entry["end_seconds"] = max(
                0.0, now - self.call_started + PHONE_FRAME_SAMPLES / PHONE_SAMPLE_RATE
            )
            item.completed.set()
            self.current = None
        return payload


class _TwilioPlayback:
    def __init__(
        self,
        timeline: dict[str, Any],
        call_started: float,
        stream_sid: str,
        send_message,
        recorder: AgentAudioRecorder,
    ) -> None:
        self.timeline = timeline
        self.call_started = call_started
        self.stream_sid = stream_sid
        self.send_message = send_message
        self.recorder = recorder
        self.pending: deque[np.ndarray] = deque()
        self.pending_samples = 0
        self.received_samples = 0
        self.played_samples = 0
        self.marks: deque[tuple[int, str]] = deque()

    def add_media(self, payload: bytes) -> None:
        samples = mulaw_to_pcm16(payload)
        self.pending.append(samples)
        self.pending_samples += len(samples)
        self.received_samples += len(samples)

    async def add_mark(self, name: str) -> None:
        if self.received_samples <= self.played_samples:
            await self.send_message(
                {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}}
            )
        else:
            self.marks.append((self.received_samples, name))

    async def clear(self) -> None:
        self.pending.clear()
        self.pending_samples = 0
        self.received_samples = self.played_samples
        now = time.monotonic()
        self.timeline["clear_events"].append({"at_seconds": max(0.0, now - self.call_started)})
        while self.marks:
            _, name = self.marks.popleft()
            await self.send_message(
                {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}}
            )

    async def play_frame(self) -> None:
        remaining = PHONE_FRAME_SAMPLES
        output = np.zeros(PHONE_FRAME_SAMPLES, dtype=np.int16)
        offset = 0
        while remaining and self.pending:
            samples = self.pending[0]
            take = min(remaining, len(samples))
            output[offset : offset + take] = samples[:take]
            if take == len(samples):
                self.pending.popleft()
            else:
                self.pending[0] = samples[take:]
            remaining -= take
            offset += take
            self.pending_samples -= take
        consumed = PHONE_FRAME_SAMPLES - remaining
        self.played_samples += consumed
        now = time.monotonic()
        await self.recorder.feed(output, now)
        while self.marks and self.marks[0][0] <= self.played_samples:
            _, name = self.marks.popleft()
            await self.send_message(
                {"event": "mark", "streamSid": self.stream_sid, "mark": {"name": name}}
            )


async def run_twilio_call(
    scenario_name: str,
    output_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:7861",
    auth_token: str = "test-auth-token",
    to_number: str = "+441234567890",
    from_number: str | None = None,
    audio_dir: str | Path = AUDIO_DIR,
    scenarios_path: str | Path = SCENARIOS_PATH,
    fake_call_log_path: str | Path | None = None,
) -> dict[str, Any]:
    scenarios = load_scenarios(scenarios_path)
    if scenario_name not in scenarios:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    scenario = scenarios[scenario_name]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    caller_number = from_number or scenario["caller_id"]
    call_sid = "CA" + uuid.uuid4().hex
    account_sid = "AC" + uuid.uuid4().hex
    stream_sid = "MZ" + uuid.uuid4().hex
    voice_url = f"{base_url.rstrip('/')}/twilio/voice"
    form = {
        "CallSid": call_sid,
        "AccountSid": account_sid,
        "From": caller_number,
        "To": to_number,
        "CallStatus": "ringing",
        "Direction": "inbound",
    }
    call_started = time.monotonic()
    timeline = _create_timeline(scenario_name, scenario["utterances"])
    timeline["transport"] = "twilio"
    timeline["clear_events"] = []
    recorder = AgentAudioRecorder(
        call_started, timeline, output / "bot_audio.wav", sample_rate=PHONE_SAMPLE_RATE
    )
    server_disconnected = asyncio.Event()
    caller_queue = _CallerAudioQueue(call_started)
    client_stopping = False
    send_lock = asyncio.Lock()
    caller_audio: list[bytes] = []
    sequence = 0
    call_lines_completed = 0
    ws = None
    tasks: list[asyncio.Task] = []

    async def send_message(payload: dict[str, Any]) -> None:
        if ws is not None and not ws.closed:
            async with send_lock:
                await ws.send_json(payload)

    def note_server_disconnect() -> None:
        if client_stopping or server_disconnected.is_set():
            return
        timeline["server_disconnect_at_seconds"] = max(0.0, time.monotonic() - call_started)
        timeline["server_disconnect_detected"] = True
        server_disconnected.set()

    try:
        async with ClientSession(timeout=ClientTimeout(total=None)) as session:
            async with session.post(
                voice_url,
                data=form,
                headers={"X-Twilio-Signature": compute_signature(auth_token, voice_url, form)},
            ) as response:
                if response.status != 200:
                    raise RuntimeError(
                        f"Twilio voice webhook failed ({response.status}): {await response.text()}"
                    )
                twiml = await response.text()
            stream_url, parameters = _parse_stream_twiml(twiml)
            ws = await session.ws_connect(stream_url)
            await send_message({"event": "connected", "protocol": "Call", "version": "1.0.0"})
            await send_message(
                {
                    "event": "start",
                    "sequenceNumber": "1",
                    "streamSid": stream_sid,
                    "start": {
                        "accountSid": account_sid,
                        "callSid": call_sid,
                        "streamSid": stream_sid,
                        "tracks": ["inbound"],
                        "mediaFormat": {
                            "encoding": "audio/x-mulaw",
                            "sampleRate": PHONE_SAMPLE_RATE,
                            "channels": 1,
                        },
                        "customParameters": parameters,
                    },
                }
            )
            ready = asyncio.Event()
            ready.set()
            playback = _TwilioPlayback(timeline, call_started, stream_sid, send_message, recorder)

            async def send_media() -> None:
                nonlocal sequence
                loop = asyncio.get_running_loop()
                started = loop.time()
                while not ws.closed and not client_stopping:
                    frame_time = started + sequence * 0.02
                    if frame_time > loop.time():
                        await asyncio.sleep(frame_time - loop.time())
                    now = time.monotonic()
                    payload = caller_queue.next_frame(now)
                    caller_audio.append(mulaw_to_pcm16(payload).tobytes())
                    sequence += 1
                    await send_message(
                        {
                            "event": "media",
                            "sequenceNumber": str(sequence + 1),
                            "streamSid": stream_sid,
                            "media": {
                                "track": "inbound",
                                "chunk": str(sequence),
                                "timestamp": str(int((loop.time() - started) * 1000)),
                                "payload": base64.b64encode(payload).decode("ascii"),
                            },
                        }
                    )

            async def receive_messages() -> None:
                try:
                    async for message in ws:
                        if message.type == WSMsgType.TEXT:
                            data = message.json()
                            event = data.get("event")
                            if event == "media":
                                media = data.get("media", {})
                                playback.add_media(
                                    base64.b64decode(media.get("payload", ""), validate=True)
                                )
                            elif event == "mark":
                                await playback.add_mark(data.get("mark", {}).get("name", ""))
                            elif event == "clear":
                                await playback.clear()
                            elif event == "stop":
                                note_server_disconnect()
                                return
                        elif message.type in {WSMsgType.CLOSE, WSMsgType.CLOSED, WSMsgType.ERROR}:
                            note_server_disconnect()
                            return
                    note_server_disconnect()
                except Exception as error:
                    if not client_stopping:
                        timeline["error"] = f"{type(error).__name__}: {error}"
                        note_server_disconnect()

            async def play_audio() -> None:
                loop = asyncio.get_running_loop()
                started = loop.time()
                tick = 0
                while not ws.closed and not client_stopping:
                    frame_time = started + tick * 0.02
                    if frame_time > loop.time():
                        await asyncio.sleep(frame_time - loop.time())
                    await playback.play_frame()
                    tick += 1

            tasks = [
                asyncio.create_task(send_media()),
                asyncio.create_task(receive_messages()),
                asyncio.create_task(play_audio()),
            ]
            transfer_observer = None
            if fake_call_log_path and scenario.get("transfer_dial_status"):
                transfer_observer = asyncio.create_task(
                    _observe_fake_transfer(
                        fake_call_log_path,
                        call_sid,
                        scenario,
                        auth_token,
                        call_started,
                        timeline,
                    )
                )
            try:
                call_lines_completed = await _drive_conversation(
                    scenario_name,
                    scenario,
                    timeline,
                    audio_dir,
                    recorder,
                    server_disconnected,
                    ready,
                    caller_queue.queue_utterance,
                )
            except Exception as error:
                timeline["error"] = f"{type(error).__name__}: {error}"
            if not server_disconnected.is_set():
                client_stopping = True
                try:
                    await send_message(
                        {
                            "event": "stop",
                            "sequenceNumber": str(sequence + 2),
                            "streamSid": stream_sid,
                        }
                    )
                except Exception:
                    pass
                await ws.close()
    except Exception as error:
        timeline["error"] = f"{type(error).__name__}: {error}"
    finally:
        client_stopping = True
        if "transfer_observer" in locals() and transfer_observer is not None:
            if not transfer_observer.done():
                try:
                    await asyncio.wait_for(transfer_observer, timeout=1)
                except TimeoutError:
                    transfer_observer.cancel()
                    await asyncio.gather(transfer_observer, return_exceptions=True)
            elif not transfer_observer.cancelled():
                transfer_observer.result()
        for task in tasks:
            if not task.done():
                task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        timeline["caller_lines_completed"] = call_lines_completed
        timeline["caller_finished_all_lines"] = call_lines_completed == len(scenario["utterances"])
        timeline["call_ended_at"] = datetime.now(UTC).isoformat()
        timeline["duration_seconds"] = max(0.0, time.monotonic() - call_started)
        timeline["server_disconnect_detected"] = server_disconnected.is_set()
        recorder._write_audio()
        _write_pcm_wave(output / "caller_audio.wav", b"".join(caller_audio), PHONE_SAMPLE_RATE)
        timeline["seconds_from_last_agent_speech_end_to_disconnect"] = (
            _seconds_from_last_agent_speech_end_to_disconnect(timeline)
        )
        (output / "caller_timeline.json").write_text(
            json.dumps(timeline, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )
    return timeline


async def _observe_fake_transfer(
    log_path: str | Path,
    call_sid: str,
    scenario: dict[str, Any],
    auth_token: str,
    call_started: float,
    timeline: dict[str, Any],
) -> None:
    deadline = time.monotonic() + 40
    path = Path(log_path)
    update = None
    while time.monotonic() < deadline:
        if path.exists():
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    entry = json.loads(line)
                    if entry.get("call_sid") == call_sid:
                        update = entry
                        break
        if update:
            break
        await asyncio.sleep(0.05)
    if not update:
        return
    twiml = update["twiml"]
    root = ET.fromstring(twiml)
    dial = root.find("./Dial")
    number = root.find("./Dial/Number")
    observed_at = max(0.0, time.monotonic() - call_started)
    transfer = {
        "observed_at_seconds": observed_at,
        "twiml": twiml,
        "dial_number": number.text if number is not None else None,
        "callback_status": None,
        "callback_response": None,
    }
    first_speech = next(
        (
            item.get("start_seconds")
            for item in timeline.get("agent_speech", [])
            if item.get("start_seconds") is not None
        ),
        None,
    )
    if first_speech is not None:
        transfer["greeting_to_transfer_seconds"] = observed_at - float(first_speech)
    callback_url = dial.get("action") if dial is not None else None
    dial_status = scenario.get("transfer_dial_status")
    if dial_status and callback_url:
        callback_form = {"CallSid": call_sid, "DialCallStatus": dial_status}
        if dial_status == "answered" and number is not None:
            callback_url = number.get("statusCallback", callback_url)
            callback_form = {
                "ParentCallSid": call_sid,
                "CallSid": "CA" + uuid.uuid4().hex,
                "CallStatus": "in-progress",
            }
        async with ClientSession() as session:
            async with session.post(
                callback_url,
                data=callback_form,
                headers={
                    "X-Twilio-Signature": compute_signature(auth_token, callback_url, callback_form)
                },
            ) as response:
                transfer["callback_status"] = response.status
                transfer["callback_response"] = await response.text()
    timeline["transfer"] = transfer


def _parse_stream_twiml(twiml: str) -> tuple[str, dict[str, str]]:
    root = ET.fromstring(twiml)
    stream = root.find("./Connect/Stream")
    if stream is None or stream.get("url") is None:
        raise ValueError("TwiML response did not contain a stream URL.")
    params = {
        parameter.get("name", ""): parameter.get("value", "")
        for parameter in stream.findall("Parameter")
    }
    return stream.get("url", ""), params


def _write_pcm_wave(path: Path, data: bytes, sample_rate: int) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with wave.open(str(path), "wb") as audio_file:
        audio_file.setnchannels(1)
        audio_file.setsampwidth(2)
        audio_file.setframerate(sample_rate)
        audio_file.writeframes(data)


async def _main(
    scenario_name: str,
    output_dir: Path,
    base_url: str,
    auth_token: str,
    to_number: str,
) -> None:
    timeline = await run_twilio_call(
        scenario_name,
        output_dir,
        base_url=base_url,
        auth_token=auth_token,
        to_number=to_number,
    )
    print(json.dumps(timeline, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run a scripted Twilio-compatible voice call.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:7861")
    parser.add_argument("--auth-token", default="test-auth-token")
    parser.add_argument("--to", default="+441234567890")
    arguments = parser.parse_args()
    asyncio.run(
        _main(
            arguments.scenario,
            arguments.output_dir,
            arguments.base_url,
            arguments.auth_token,
            arguments.to,
        )
    )


if __name__ == "__main__":
    main()
