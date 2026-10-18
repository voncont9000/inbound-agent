from inbound_core.languages import MULTI_LANGUAGES
from loguru import logger
from pipecat.frames.frames import TranscriptionFrame, TTSUpdateSettingsFrame
from pipecat.processors.frame_processor import FrameDirection, FrameProcessor
from pipecat.services.speechify.tts import SpeechifyHttpTTSService

_SPEECHIFY_LANGUAGE_TAGS = {
    "de": "de-DE",
    "es": "es-ES",
    "fr": "fr-FR",
    "it": "it-IT",
    "pt": "pt-BR",
}


class LanguageSwitchProcessor(FrameProcessor):
    def __init__(self, tts: SpeechifyHttpTTSService) -> None:
        super().__init__()
        self._tts = tts
        self._current_language: str | None = None
        self._warned_missing_language = False

    async def process_frame(self, frame, direction: FrameDirection) -> None:
        await super().process_frame(frame, direction)
        if not isinstance(frame, TranscriptionFrame) or direction != FrameDirection.DOWNSTREAM:
            await self.push_frame(frame, direction)
            return

        detected_language = frame.language
        if detected_language is None:
            if not self._warned_missing_language:
                logger.warning(
                    "STT did not provide language annotations; using the configured voice "
                    "locale and multilingual prompt without runtime voice switching."
                )
                self._warned_missing_language = True
            await self.push_frame(frame, direction)
            return

        language_code = str(getattr(detected_language, "value", detected_language))
        base_language = language_code.casefold().split("-", maxsplit=1)[0]
        if base_language not in MULTI_LANGUAGES:
            await self.push_frame(frame, direction)
            return

        if base_language != self._current_language:
            update = TTSUpdateSettingsFrame(
                delta=SpeechifyHttpTTSService.Settings(
                    model="simba-3.0",
                    language=_SPEECHIFY_LANGUAGE_TAGS.get(base_language),
                ),
                service=self._tts,
            )
            await self.push_frame(update, direction)
            self._current_language = base_language

        await self.push_frame(frame, direction)
