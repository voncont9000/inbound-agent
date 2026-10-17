import asyncio
import time

import numpy as np
import pytest

from eval.caller import (
    AgentAudioRecorder,
    _seconds_from_last_agent_speech_end_to_disconnect,
    _wait_for_agent_speech_or_disconnect,
    _wait_for_event_or_disconnect,
    final_call_timeout_seconds,
)


def test_wait_for_speech_allows_long_agent_turn_after_speech_starts(tmp_path) -> None:
    async def scenario() -> None:
        recorder = AgentAudioRecorder(
            time.monotonic(),
            {"agent_speech": []},
            tmp_path / "bot_audio.wav",
        )
        wait = asyncio.create_task(recorder.wait_for_speech_after(0, 0.5, 1.5))
        await asyncio.sleep(0.02)
        now = time.monotonic()
        await recorder._observe_energy(np.full(960, 1000, dtype=np.int16), now)
        await asyncio.sleep(0.05)
        await recorder._finish_speech(time.monotonic(), time.monotonic())
        await wait

    asyncio.run(scenario())


def test_wait_for_speech_times_out_when_agent_never_starts(tmp_path) -> None:
    async def scenario() -> None:
        recorder = AgentAudioRecorder(
            time.monotonic(), {"agent_speech": []}, tmp_path / "audio.wav"
        )
        with pytest.raises(TimeoutError, match="speech to start"):
            await recorder.wait_for_speech_after(0, 0.01, 0.1)

    asyncio.run(scenario())


def test_scripted_caller_stops_waiting_when_server_disconnects() -> None:
    async def scenario() -> None:
        completed = asyncio.Event()
        server_disconnected = asyncio.Event()
        server_disconnected.set()

        assert not await _wait_for_event_or_disconnect(completed, server_disconnected, timeout=1)

        class SilentRecorder:
            async def wait_for_speech_after(self, *_args, **_kwargs) -> None:
                await asyncio.Event().wait()

        assert not await _wait_for_agent_speech_or_disconnect(
            SilentRecorder(), 0, server_disconnected
        )

    asyncio.run(scenario())


def test_disconnect_gap_uses_last_completed_agent_speech() -> None:
    timeline = {
        "server_disconnect_detected": True,
        "server_disconnect_at_seconds": 16.0,
        "agent_speech": [
            {"end_seconds": 5.0},
            {"end_seconds": 13.5},
            {"end_seconds": None},
        ],
    }

    assert _seconds_from_last_agent_speech_end_to_disconnect(timeline) == 2.5
    assert (
        _seconds_from_last_agent_speech_end_to_disconnect({"server_disconnect_detected": False})
        is None
    )


def test_final_call_timeout_can_be_overridden_per_scenario() -> None:
    assert final_call_timeout_seconds({}) == 25
    assert final_call_timeout_seconds({"final_call_timeout_seconds": 35}) == 35
