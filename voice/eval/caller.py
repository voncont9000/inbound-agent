from __future__ import annotations

import argparse
import asyncio
import json
import time
import wave
from collections import deque
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

from eval.synth_callers import AUDIO_DIR, SCENARIOS_PATH, WEBRTC_SAMPLE_RATE, load_scenarios

FRAME_SAMPLES = WEBRTC_SAMPLE_RATE // 50
SPEECH_ENERGY_THRESHOLD = 250.0
SILENCE_SECONDS = 1.2
TURN_TIMEOUT_SECONDS = 25
FINAL_CALL_TIMEOUT_SECONDS = 15


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
        item = PlaybackItem(
            samples=np.asarray(samples, dtype=np.int16).reshape(-1),
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


class AgentAudioRecorder:
    def __init__(self, call_started: float, timeline: dict[str, Any], output_path: Path) -> None:
        self._call_started = call_started
        self._timeline = timeline
        self._output_path = output_path
        self._condition = asyncio.Condition()
        self._sample_chunks: list[bytes] = []
        self._completed_speeches = 0
        self._speaking: dict[str, Any] | None = None
        self._last_active_at: float | None = None
        self.ended = asyncio.Event()

    @property
    def completed_speeches(self) -> int:
        return self._completed_speeches

    async def record(self, track: Any) -> None:
        resampler = AudioResampler("s16", "mono", WEBRTC_SAMPLE_RATE)
        try:
            while True:
                frame = await track.recv()
                for output_frame in resampler.resample(frame):
                    samples = output_frame.to_ndarray().reshape(-1).astype(np.int16)
                    self._sample_chunks.append(samples.tobytes())
                    await self._observe_energy(samples, time.monotonic())
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

    async def wait_for_speech_after(self, previous_count: int, timeout: float) -> None:
        async with self._condition:
            await asyncio.wait_for(
                self._condition.wait_for(
                    lambda: self._completed_speeches > previous_count or self.ended.is_set()
                ),
                timeout=timeout,
            )
        if self._completed_speeches <= previous_count:
            raise TimeoutError("No completed agent speech was detected.")

    def _write_audio(self) -> None:
        self._output_path.parent.mkdir(parents=True, exist_ok=True)
        with wave.open(str(self._output_path), "wb") as audio_file:
            audio_file.setnchannels(1)
            audio_file.setsampwidth(2)
            audio_file.setframerate(WEBRTC_SAMPLE_RATE)
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
    timeline: dict[str, Any] = {
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
        "error": None,
    }
    peer = RTCPeerConnection()
    caller_track = ScriptedAudioTrack(call_started, timeline)
    peer.addTrack(caller_track)
    peer.createDataChannel("chat", ordered=True)

    recorder = AgentAudioRecorder(call_started, timeline, output / "bot_audio.wav")
    recording_tasks: list[asyncio.Task[None]] = []
    audio_track_event = asyncio.Event()
    server_disconnected = asyncio.Event()

    @peer.on("track")
    def on_track(track: Any) -> None:
        if track.kind == "audio":
            audio_track_event.set()
            recording_tasks.append(asyncio.create_task(recorder.record(track)))

    @peer.on("connectionstatechange")
    async def on_connectionstatechange() -> None:
        if peer.connectionState in {"disconnected", "failed", "closed"}:
            server_disconnected.set()

    session_timeout = ClientTimeout(total=20)
    try:
        async with ClientSession(timeout=session_timeout) as session:
            await _connect(peer, base_url, session)
            try:
                await asyncio.wait_for(audio_track_event.wait(), timeout=TURN_TIMEOUT_SECONDS)
                await recorder.wait_for_speech_after(0, TURN_TIMEOUT_SECONDS)
            except TimeoutError as error:
                raise TimeoutError("Timed out waiting for the agent's greeting.") from error

            for index in range(len(utterances)):
                audio_path = Path(audio_dir) / scenario_name / f"{index}.wav"
                if not audio_path.exists():
                    raise FileNotFoundError(f"Missing synthesized caller audio: {audio_path}")
                previous_speeches = recorder.completed_speeches
                item = caller_track.queue_utterance(
                    _read_caller_audio(audio_path),
                    timeline["utterances"][index],
                )
                assert item.completed is not None
                try:
                    await asyncio.wait_for(item.completed.wait(), timeout=30)
                except TimeoutError as error:
                    raise TimeoutError(
                        f"Caller utterance {index} did not finish playback."
                    ) from error

                if index < len(utterances) - 1:
                    try:
                        await recorder.wait_for_speech_after(
                            previous_speeches, TURN_TIMEOUT_SECONDS
                        )
                    except TimeoutError as error:
                        raise TimeoutError(
                            f"Timed out waiting for the agent response after utterance {index}."
                        ) from error
                else:
                    try:
                        await asyncio.wait_for(
                            server_disconnected.wait(),
                            timeout=FINAL_CALL_TIMEOUT_SECONDS,
                        )
                    except TimeoutError:
                        pass
                    timeline["server_disconnect_detected"] = server_disconnected.is_set()
    except Exception as error:
        timeline["error"] = f"{type(error).__name__}: {error}"
    finally:
        timeline["call_ended_at"] = datetime.now(UTC).isoformat()
        timeline["duration_seconds"] = max(0.0, time.monotonic() - call_started)
        timeline["server_disconnect_detected"] = server_disconnected.is_set()
        await peer.close()
        for task in recording_tasks:
            try:
                await asyncio.wait_for(task, timeout=3)
            except (TimeoutError, MediaStreamError):
                task.cancel()
        if not (output / "bot_audio.wav").exists():
            recorder._write_audio()
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
