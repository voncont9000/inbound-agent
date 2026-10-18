import asyncio
import wave

from pipecat.processors.audio.audio_buffer_processor import AudioBufferProcessor

from inbound_voice.recording import (
    add_recording_processor,
    create_recording_processor,
    recording_duration_seconds,
    upload_recording_and_cleanup,
)
from inbound_voice.sinks import LocalFilesCallSink


def test_recording_processor_writes_mono_16_bit_wav(tmp_path) -> None:
    async def run() -> None:
        sink = LocalFilesCallSink(tmp_path, "recording")
        processor = create_recording_processor(sink)
        assert isinstance(processor, AudioBufferProcessor)
        path = sink.directory / "recording.wav"
        await processor._call_event_handler("on_audio_data", b"\x01\x00" * 800, 8000, 1)
        await asyncio.gather(*(task for _, task in processor._event_tasks))
        with wave.open(str(path), "rb") as recording:
            assert recording.getnchannels() == 1
            assert recording.getsampwidth() == 2
            assert recording.getframerate() == 8000
            assert recording.getnframes() == 800
        assert recording_duration_seconds(path) == 0.1

    asyncio.run(run())


def test_recording_processor_is_inserted_only_when_enabled() -> None:
    output = object()
    assistant_aggregator = object()
    processors = [object(), output, assistant_aggregator]

    assert add_recording_processor(processors, None) is processors
    recorder = object()
    assert add_recording_processor(processors, recorder) == [
        processors[0],
        output,
        recorder,
        assistant_aggregator,
    ]


def test_recording_upload_deletes_only_after_success(tmp_path) -> None:
    class FakeApi:
        def __init__(self, result: bool) -> None:
            self.result = result

        async def upload_recording(
            self,
            conversation_id,
            external_call_id,
            path,
            *,
            duration_seconds,
        ) -> bool:
            return self.result

    async def run() -> None:
        success_path = tmp_path / "uploaded.wav"
        success_path.write_bytes(b"wav")
        failed_path = tmp_path / "failed.wav"
        failed_path.write_bytes(b"wav")

        assert await upload_recording_and_cleanup(
            FakeApi(True),
            "conversation-id",
            "call-id",
            success_path,
            duration_seconds=1,
        )
        assert not success_path.exists()
        assert not await upload_recording_and_cleanup(
            FakeApi(False),
            "conversation-id",
            "call-id",
            failed_path,
            duration_seconds=1,
        )
        assert failed_path.exists()

    asyncio.run(run())
