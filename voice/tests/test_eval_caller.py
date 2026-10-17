import asyncio
import time

import numpy as np
import pytest

from eval.caller import AgentAudioRecorder


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
