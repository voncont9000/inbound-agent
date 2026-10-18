from __future__ import annotations

import logging
import wave
from pathlib import Path
from typing import Any
from uuid import UUID

from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from inbound_voice.sinks import LocalFilesCallSink

LOGGER = logging.getLogger(__name__)


def add_recording_processor(processors: list[Any], recording_processor: Any | None) -> list[Any]:
    if recording_processor is None:
        return processors
    return [*processors[:-1], recording_processor, processors[-1]]


def create_recording_processor(sink: LocalFilesCallSink) -> AudioBufferProcessor:
    processor = AudioBufferProcessor(num_channels=1, buffer_size=0)

    @processor.event_handler("on_audio_data")
    async def on_audio_data(
        _processor: AudioBufferProcessor, audio: bytes, sample_rate: int, num_channels: int
    ) -> None:
        if not audio:
            return
        path = sink.directory / "recording.wav"
        with wave.open(str(path), "wb") as output:
            output.setnchannels(1)
            output.setsampwidth(2)
            output.setframerate(sample_rate)
            output.writeframes(audio)

    return processor


def recording_duration_seconds(path: str | Path) -> float:
    with wave.open(str(path), "rb") as recording:
        if recording.getframerate() <= 0:
            return 0.0
        return recording.getnframes() / recording.getframerate()


async def upload_recording_and_cleanup(
    api: object,
    conversation_id: UUID,
    external_call_id: str,
    path: str | Path,
    *,
    duration_seconds: float,
) -> bool:
    recording_path = Path(path)
    try:
        uploaded = await api.upload_recording(
            conversation_id,
            external_call_id,
            recording_path,
            duration_seconds=duration_seconds,
        )
    except Exception:
        LOGGER.warning(
            "Could not upload voice recording for call %s", external_call_id, exc_info=True
        )
        return False
    if uploaded:
        try:
            recording_path.unlink(missing_ok=True)
        except OSError:
            LOGGER.warning(
                "Could not delete uploaded voice recording for call %s",
                external_call_id,
                exc_info=True,
            )
            return False
        return True
    LOGGER.warning("Could not upload voice recording for call %s", external_call_id)
    return False


__all__ = [
    "add_recording_processor",
    "create_recording_processor",
    "recording_duration_seconds",
    "upload_recording_and_cleanup",
]
