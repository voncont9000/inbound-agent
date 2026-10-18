from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from collections import deque
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from fractions import Fraction
from pathlib import Path
from typing import Any

import numpy as np
from aiohttp import ClientSession, ClientTimeout
from aiortc import RTCPeerConnection, RTCSessionDescription
from aiortc.mediastreams import AudioStreamTrack, MediaStreamError
from av import AudioFrame, AudioResampler

from eval.synth_callers import (
    AUDIO_DIR,
    SCENARIOS_PATH,
    WEBRTC_SAMPLE_RATE,
    load_scenarios,
    utterance_audio_path,
)

FRAME_SAMPLES = WEBRTC_SAMPLE_RATE // 50
SPEECH_ENERGY_THRESHOLD = 250.0
SILENCE_SECONDS = 1.2
TURN_TIMEOUT_SECONDS = 25
MAX_AGENT_TURN_SECONDS = 90
FINAL_CALL_TIMEOUT_SECONDS = 25


def _speech_segment_offsets(samples: np.ndarray) -> list[tuple[int, int]]:
    frame_size = WEBRTC_SAMPLE_RATE // 100
    energies = [
        float(np.sqrt(np.mean(np.square(samples[index : index + frame_size].astype(np.float32)))))
        for index in range(0, len(samples), frame_size)
    ]
    active = [energy >= SPEECH_ENERGY_THRESHOLD for energy in energies]
    runs: list[tuple[int, int]] = []
    index = 0
    while index < len(active):
        if not active[index]:
            index += 1
            continue
        start = index
        while index < len(active) and active[index]:
            index += 1
        runs.append((start * frame_size, min(index * frame_size, len(samples))))
    merged: list[tuple[int, int]] = []
    for start, end in runs:
        if merged and start - merged[-1][1] < WEBRTC_SAMPLE_RATE // 2:
            merged[-1] = (merged[-1][0], end)
        else:
            merged.append((start, end))
    return merged


@dataclass
class PlaybackItem:
    samples: np.ndarray
    timeline_entry: dict[str, Any]
    cursor: int = 0
    completed: asyncio.Event | None = None


class ScriptedAudioTrack(AudioStreamTrack):
    """Send 48 kHz mono caller audio, filling idle frames with continuous silence."""

    def __init__(self, call_started: float, timeline: dict[str, Any]) -> None:
        super().__init__()
        self._call_started = call_started
        self._timeline = timeline
        self._queued: deque[PlaybackItem] = deque()
        self._current: PlaybackItem | None = None
        self._timestamp = 0
        self._track_started: float | None = None

    def queue_utterance(self, samples: np.ndarray, timeline_entry: dict[str, Any]) -> PlaybackItem:
        normalized_samples = np.asarray(samples, dtype=np.int16).reshape(-1)
        if "| pause " in str(timeline_entry.get("text", "")):
            timeline_entry["segments"] = [
                {
                    "start_offset_seconds": start / WEBRTC_SAMPLE_RATE,
                    "end_offset_seconds": end / WEBRTC_SAMPLE_RATE,
                    "start_seconds": None,
                    "end_seconds": None,
                }
                for start, end in _speech_segment_offsets(normalized_samples)
            ]
        item = PlaybackItem(
            samples=normalized_samples,
            timeline_entry=timeline_entry,
            completed=asyncio.Event(),
        )
        self._queued.append(item)
        return item

    async def recv(self) -> AudioFrame:
        if self.readyState != "live":
            raise MediaStreamError

        loop = asyncio.get_running_loop()
        if self._track_started is None:
            self._track_started = loop.time()
        frame_time = self._track_started + self._timestamp / WEBRTC_SAMPLE_RATE
        delay = frame_time - loop.time()
        if delay > 0:
            await asyncio.sleep(delay)

        samples = np.zeros(FRAME_SAMPLES, dtype=np.int16)
        if self._current is None and self._queued:
            self._current = self._queued.popleft()

        if self._current is not None:
            item = self._current
            if item.timeline_entry["start_seconds"] is None:
                item.timeline_entry["start_seconds"] = max(0.0, loop.time() - self._call_started)
                for segment in item.timeline_entry.get("segments", []):
                    segment["start_seconds"] = (
                        item.timeline_entry["start_seconds"] + segment["start_offset_seconds"]
                    )
                    segment["end_seconds"] = (
                        item.timeline_entry["start_seconds"] + segment["end_offset_seconds"]
                    )
            count = min(FRAME_SAMPLES, len(item.samples) - item.cursor)
            samples[:count] = item.samples[item.cursor : item.cursor + count]
            item.cursor += count
            if item.cursor >= len(item.samples):
                item.timeline_entry["end_seconds"] = (
                    max(0.0, loop.time() - self._call_started) + count / WEBRTC_SAMPLE_RATE
                )
                if item.completed is not None:
                    item.completed.set()
                self._current = None

        frame = AudioFrame.from_ndarray(samples.reshape(1, -1), format="s16", layout="mono")
        frame.sample_rate = WEBRTC_SAMPLE_RATE
        frame.pts = self._timestamp
        frame.time_base = Fraction(1, WEBRTC_SAMPLE_RATE)
        self._timestamp += FRAME_SAMPLES
        return frame


async def _wait_for_event_or_disconnect(
    event: asyncio.Event,
    server_disconnected: asyncio.Event,
    timeout: float,
) -> bool:
    if event.is_set():
        return True
    if server_disconnected.is_set():
        return False

    event_task = asyncio.create_task(event.wait())
    disconnect_task = asyncio.create_task(server_disconnected.wait())
    done, pending = await asyncio.wait(
        {event_task, disconnect_task},
        timeout=timeout,
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if not done:
        raise TimeoutError("Timed out waiting for caller or server event.")
    return event.is_set()


async def _wait_for_agent_speech_or_disconnect(
    recorder: AgentAudioRecorder,
    previous_speeches: int,
    server_disconnected: asyncio.Event,
) -> bool:
    speech_task = asyncio.create_task(
        recorder.wait_for_speech_after(
            previous_speeches, TURN_TIMEOUT_SECONDS, MAX_AGENT_TURN_SECONDS
        )
    )
    disconnect_task = asyncio.create_task(server_disconnected.wait())
    done, pending = await asyncio.wait(
        {speech_task, disconnect_task},
        return_when=asyncio.FIRST_COMPLETED,
    )
    for task in pending:
        task.cancel()
    if pending:
        await asyncio.gather(*pending, return_exceptions=True)
    if disconnect_task in done and speech_task not in done:
        return False
    await speech_task
    return True


def _seconds_from_last_agent_speech_end_to_disconnect(
    timeline: dict[str, Any],
) -> float | None:
    if not timeline.get("server_disconnect_detected"):
        return None
    disconnect_at = timeline.get("server_disconnect_at_seconds")
    speech_ends = [
        float(speech["end_seconds"])
        for speech in timeline.get("agent_speech", [])
        if speech.get("end_seconds") is not None
    ]
    if disconnect_at is None or not speech_ends:
        return None
    return max(0.0, float(disconnect_at) - max(speech_ends))


def final_call_timeout_seconds(scenario: dict[str, Any]) -> float:
    return float(scenario.get("final_call_timeout_seconds", FINAL_CALL_TIMEOUT_SECONDS))


class AgentAudioRecorder:
    def __init__(
        self,
        call_started: float,
        timeline: dict[str, Any],
        output_path: Path,
        sample_rate: int = WEBRTC_SAMPLE_RATE,
    ) -> None:
        self._call_started = call_started
        self._timeline = timeline
        self._output_path = output_path
        self.sample_rate = sample_rate
        self._condition = asyncio.Condition()
        self._sample_chunks: list[bytes] = []
        self._completed_speeches = 0
        self._started_speeches = 0
        self._speaking: dict[str, Any] | None = None
        self._last_active_at: float | None = None
        self.ended = asyncio.Event()

    @property
    def completed_speeches(self) -> int:
        return self._completed_speeches

    async def record(self, track: Any) -> None:
        resampler = AudioResampler("s16", "mono", self.sample_rate)
        try:
            while True:
                frame = await track.recv()
                for output_frame in resampler.resample(frame):
                    samples = output_frame.to_ndarray().reshape(-1).astype(np.int16)
                    await self.feed(samples, time.monotonic())
        except MediaStreamError:
            pass
        finally:
            if self._speaking is not None:
                await self._finish_speech(
                    self._last_active_at or time.monotonic(),
                    time.monotonic(),
                )
            self._write_audio()
            self.ended.set()
            async with self._condition:
                self._condition.notify_all()

    async def feed(self, samples: np.ndarray, now: float) -> None:
        normalized = np.asarray(samples, dtype=np.int16).reshape(-1)
        self._sample_chunks.append(normalized.tobytes())
        await self._observe_energy(normalized, now)

    async def _observe_energy(self, samples: np.ndarray, now: float) -> None:
        if not samples.size:
            return
        energy = float(np.sqrt(np.mean(np.square(samples.astype(np.float32)))))
        if energy >= SPEECH_ENERGY_THRESHOLD:
            if self._speaking is None:
                self._speaking = {
                    "start_seconds": max(0.0, now - self._call_started),
                    "end_seconds": None,
                    "silence_detected_at_seconds": None,
                }
                self._timeline["agent_speech"].append(self._speaking)
                self._started_speeches += 1
                async with self._condition:
                    self._condition.notify_all()
            self._last_active_at = now
        elif (
            self._speaking is not None
            and self._last_active_at is not None
            and now - self._last_active_at >= SILENCE_SECONDS
        ):
            await self._finish_speech(self._last_active_at, now)

    async def _finish_speech(self, last_active: float, detected_at: float) -> None:
        if self._speaking is None:
            return
        self._speaking["end_seconds"] = max(0.0, last_active - self._call_started)
        self._speaking["silence_detected_at_seconds"] = max(0.0, detected_at - self._call_started)
        self._speaking = None
        self._last_active_at = None
        async with self._condition:
            self._completed_speeches += 1
            self._condition.notify_all()

    async def wait_for_speech_after(
        self,
        previous_count: int,
        start_timeout: float,
        max_turn_timeout: float = MAX_AGENT_TURN_SECONDS,
    ) -> None:
        loop = asyncio.get_running_loop()
        deadline = loop.time() + max_turn_timeout
        async with self._condition:
            try:
                await asyncio.wait_for(
                    self._condition.wait_for(
                        lambda: self._started_speeches > previous_count or self.ended.is_set()
                    ),
                    timeout=min(start_timeout, max_turn_timeout),
                )
            except TimeoutError as error:
                raise TimeoutError("Timed out waiting for agent speech to start.") from error
            if self._completed_speeches <= previous_count:
                remaining = deadline - loop.time()
                if remaining <= 0:
                    raise TimeoutError("Timed out waiting for agent speech to complete.")
                try:
                    await asyncio.wait_for(
                        self._condition.wait_for(
                            lambda: self._completed_speeches > previous_count or self.ended.is_set()
                        ),
                        timeout=remaining,
                    )
                except TimeoutError as error:
                    raise TimeoutError("Timed out waiting for agent speech to complete.") from error
        if self._completed_speeches <= previous_count:
            raise TimeoutError("No completed agent speech was detected.")

    def _write_audio(self) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self._output_path), "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)
            audio_file.setframerate(self.sample_rate)
            audio_file.writeframes(b"".join(self._sample_chunks))


def _read_caller_audio(path: Path) -> np.ndarray:
    with wave.open(str(path), "rb") as audio_file:
        channels = audio_file.getnchannels()
        sample_width = audio_file.getsampwidth()
        sample_rate = audio_file.getframerate()
        frames = audio_file.readframes(audio_file.getnframes())
    if sample_width != 2:
        raise ValueError(f"Expected 16-bit caller audio in {path}, got {sample_width * 8}-bit.")
    samples = np.frombuffer(frames, dtype=np.int16)
    if channels > 1:
        samples = samples.reshape(-1, channels).mean(axis=1).astype(np.int16)
    if sample_rate != WEBRTC_SAMPLE_RATE:
        raise ValueError(
            f"Expected {WEBRTC_SAMPLE_RATE} Hz caller audio in {path}, got {sample_rate} Hz."
        )
    return samples


def _create_timeline(scenario_name: str, utterances: list[str]) -> dict[str, Any]:
    return {
        "scenario": scenario_name,
        "call_started_at": datetime.now(UTC).isoformat(),
        "utterances": [
            {
                "index": index,
                "text": text,
                "start_seconds": None,
                "end_seconds": None,
            }
            for index, text in enumerate(utterances)
        ],
        "agent_speech": [],
        "server_disconnect_detected": False,
        "server_disconnect_at_seconds": None,
        "seconds_from_last_agent_speech_end_to_disconnect": None,
        "number_holds": [],
        "caller_lines_completed": 0,
        "caller_finished_all_lines": False,
        "error": None,
    }


async def _drive_conversation(
    scenario_name: str,
    scenario: dict[str, Any],
    timeline: dict[str, Any],
    audio_dir: str | Path,
    recorder: AgentAudioRecorder,
    server_disconnected: asyncio.Event,
    ready: asyncio.Event,
    queue_utterance: Callable[[np.ndarray, dict[str, Any]], asyncio.Event],
) -> int:
    utterances = scenario["utterances"]
    ready_for_greeting = await _wait_for_event_or_disconnect(
        ready, server_disconnected, TURN_TIMEOUT_SECONDS
    )
    if ready_for_greeting:
        greeting_spoken = await _wait_for_agent_speech_or_disconnect(
            recorder, 0, server_disconnected
        )
        if not greeting_spoken:
            return 0

    caller_lines_completed = 0
    if ready_for_greeting and not server_disconnected.is_set():
        for index, utterance in enumerate(utterances):
            if server_disconnected.is_set():
                break
            audio_path = utterance_audio_path(
                audio_dir,
                scenario_name,
                index,
                utterance,
                scenario["caller_voice"],
            )
            if not audio_path.exists():
                raise FileNotFoundError(f"Missing synthesized caller audio: {audio_path}")
            previous_speeches = recorder.completed_speeches
            completed = queue_utterance(
                _read_caller_audio(audio_path), timeline["utterances"][index]
            )
            try:
                played = await _wait_for_event_or_disconnect(
                    completed, server_disconnected, timeout=30
                )
            except TimeoutError as error:
                raise TimeoutError(f"Caller utterance {index} did not finish playback.") from error
            if not played:
                break
            caller_lines_completed += 1

            if index < len(utterances) - 1:
                try:
                    response_spoken = await _wait_for_agent_speech_or_disconnect(
                        recorder, previous_speeches, server_disconnected
                    )
                    if not response_spoken:
                        break
                except TimeoutError as error:
                    if not server_disconnected.is_set():
                        raise TimeoutError(
                            f"Timed out waiting for the agent response after utterance {index}."
                        ) from error
                    break
            else:
                try:
                    await asyncio.wait_for(
                        server_disconnected.wait(),
                        timeout=final_call_timeout_seconds(scenario),
                    )
                except TimeoutError:
                    pass
    return caller_lines_completed


async def _connect(
    peer: RTCPeerConnection,
    base_url: str,
    session: ClientSession,
) -> None:
    offer = await peer.createOffer()
    await peer.setLocalDescription(offer)
    local_description = peer.localDescription
    if local_description is None:
        raise RuntimeError("aiortc did not produce a local WebRTC offer.")
    payload = {
        "sdp": local_description.sdp,
        "type": local_description.type,
        "pc_id": None,
        "restart_pc": False,
    }
    async with session.post(f"{base_url.rstrip('/')}/api/offer", json=payload) as response:
        if response.status >= 400:
            details = await response.text()
            raise RuntimeError(f"SmallWebRTC offer failed ({response.status}): {details}")
        answer = await response.json()
    await peer.setRemoteDescription(RTCSessionDescription(sdp=answer["sdp"], type=answer["type"]))


async def run_call(
    scenario_name: str,
    output_dir: str | Path,
    *,
    base_url: str = "http://127.0.0.1:7860",
    audio_dir: str | Path = AUDIO_DIR,
    scenarios_path: str | Path = SCENARIOS_PATH,
) -> dict[str, Any]:
    scenarios = load_scenarios(scenarios_path)
    if scenario_name not in scenarios:
        raise ValueError(f"Unknown scenario: {scenario_name}")
    scenario = scenarios[scenario_name]
    utterances = scenario["utterances"]
    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)

    call_started = time.monotonic()
    timeline = _create_timeline(scenario_name, utterances)
    peer = RTCPeerConnection()
    caller_track = ScriptedAudioTrack(call_started, timeline)
    peer.addTrack(caller_track)
    peer.createDataChannel("chat", ordered=True)

    recorder = AgentAudioRecorder(
        call_started,
        timeline,
        output / "bot_audio.wav",
        sample_rate=WEBRTC_SAMPLE_RATE,
    )
    recording_tasks: list[asyncio.Task[None]] = []
    audio_track_event = asyncio.Event()
    server_disconnected = asyncio.Event()
    closing_peer = False

    @peer.on("track")
    def on_track(track: Any) -> None:
        if track.kind == "audio":
            audio_track_event.set()
            recording_tasks.append(asyncio.create_task(recorder.record(track)))

    @peer.on("connectionstatechange")
    async def on_connectionstatechange() -> None:
        if (
            not closing_peer
            and peer.connectionState in {"disconnected", "failed", "closed"}
            and not server_disconnected.is_set()
        ):
            timeline["server_disconnect_at_seconds"] = max(0.0, time.monotonic() - call_started)
            timeline["server_disconnect_detected"] = True
            server_disconnected.set()

    session_timeout = ClientTimeout(total=20)
    caller_lines_completed = 0

    def queue_utterance(samples: np.ndarray, entry: dict[str, Any]) -> asyncio.Event:
        item = caller_track.queue_utterance(samples, entry)
        assert item.completed is not None
        return item.completed

    try:
        async with ClientSession(timeout=session_timeout) as session:
            await _connect(peer, base_url, session)
            try:
                caller_lines_completed = await _drive_conversation(
                    scenario_name,
                    scenario,
                    timeline,
                    audio_dir,
                    recorder,
                    server_disconnected,
                    audio_track_event,
                    queue_utterance,
                )
            except TimeoutError as error:
                if not server_disconnected.is_set():
                    raise TimeoutError("Timed out waiting for the agent's greeting.") from error
    except Exception as error:
        timeline["error"] = f"{type(error).__name__}: {error}"
    finally:
        timeline["caller_lines_completed"] = caller_lines_completed
        timeline["caller_finished_all_lines"] = caller_lines_completed == len(utterances)
        timeline["call_ended_at"] = datetime.now(UTC).isoformat()
        timeline["duration_seconds"] = max(0.0, time.monotonic() - call_started)
        closing_peer = True
        timeline["server_disconnect_detected"] = server_disconnected.is_set()
        await peer.close()
        for task in recording_tasks:
            try:
                await asyncio.wait_for(task, timeout=3)
            except (TimeoutError, MediaStreamError):
                task.cancel()
        if not (output / "bot_audio.wav").exists():
            recorder._write_audio()
        timeline["seconds_from_last_agent_speech_end_to_disconnect"] = (
            _seconds_from_last_agent_speech_end_to_disconnect(timeline)
        )
        (output / "caller_timeline.json").write_text(
            json.dumps(timeline, indent=2, ensure_ascii=False) + "\n",
            encoding="utf-8",
        )

    return timeline


async def _main(scenario_name: str, base_url: str, output_dir: Path) -> None:
    timeline = await run_call(scenario_name, output_dir, base_url=base_url)
    print(json.dumps(timeline, indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description="Run one scripted SmallWebRTC caller.")
    parser.add_argument("--scenario", required=True)
    parser.add_argument("--base-url", default="http://127.0.0.1:7860")
    parser.add_argument("--output-dir", type=Path, required=True)
    arguments = parser.parse_args()
    asyncio.run(_main(arguments.scenario, arguments.base_url, arguments.output_dir))


if __name__ == "__main__":
    main()
