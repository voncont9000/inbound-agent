import asyncio

from pipecat.frames.frames import Frame, TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection
from pipecat.transcriptions.language import Language

from inbound_voice.language_switch import LanguageSwitchProcessor


class RecordingLanguageSwitchProcessor(LanguageSwitchProcessor):
    def __init__(self, tts: object) -> None:
        super().__init__(tts)
        self.frames: list[tuple[Frame, FrameDirection]] = []

    async def push_frame(self, frame: Frame, direction: FrameDirection) -> None:
        self.frames.append((frame, direction))


def test_language_switch_updates_speechify_only_when_base_language_changes() -> None:
    async def process_frames() -> tuple[RecordingLanguageSwitchProcessor, object]:
        tts = object()
        processor = RecordingLanguageSwitchProcessor(tts)
        for language in (Language.DE, Language.DE, Language.FR, Language.EN):
            frame = TranscriptionFrame("Hello", "caller", "now", language)
            await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
        return processor, tts

    processor, tts = asyncio.run(process_frames())
    updates = [frame for frame, _ in processor.frames if isinstance(frame, TTSUpdateSettingsFrame)]

    assert [update.delta.language for update in updates] == ["de-DE", "fr-FR", None]
    assert all(update.delta.model == "simba-3.0" for update in updates)
    assert all(update.service is tts for update in updates)
    transcribed = [
        frame.text for frame, _ in processor.frames if isinstance(frame, TranscriptionFrame)
    ]
    assert transcribed == ["Hello", "Hello", "Hello", "Hello"]


def test_language_switch_ignores_missing_and_unsupported_annotations() -> None:
    async def process_frames() -> RecordingLanguageSwitchProcessor:
        processor = RecordingLanguageSwitchProcessor(object())
        for language in (None, Language.JA):
            frame = TranscriptionFrame("Hello", "caller", "now", language)
            await processor.process_frame(frame, FrameDirection.DOWNSTREAM)
        return processor

    processor = asyncio.run(process_frames())

    assert not any(isinstance(frame, TTSUpdateSettingsFrame) for frame, _ in processor.frames)
    assert len(processor.frames) == 2
