from collections import deque
from collections.abc import AsyncGenerator, Mapping
from dataclasses import dataclass
from math import ceil, sqrt
from struct import iter_unpack
from typing import Any
from xml.sax.saxutils import escape

import aiohttp
from inbound_core.config import AssistantConfig, PronunciationEntry
from inbound_core.languages import (
    DEFAULT_LOCALE,
    DEFAULT_VOICE_ID,
    LOCALES,
    MULTI_LANGUAGES,
    Locale,
)
from inbound_core.voice_pace import speechify_rate_percent, speechify_voice_wpm
from loguru import logger
from pipecat.frames.frames import Frame, TTSAudioRawFrame
from pipecat.services.assemblyai.stt import AssemblyAISTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService, ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService, GoogleThinkingConfig
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.services.soniox.stt import SonioxContextObject, SonioxSTTService
from pipecat.services.speechify.tts import SpeechifyHttpTTSService
from pipecat.transcriptions.language import Language

from inbound_voice.pronunciation import PronunciationTransform

DEEPGRAM_TTS_VOICES = {
    "de": "aura-2-julius-de",
    "es-ES": "aura-2-nestor-es",
    "es-MX": "aura-2-estrella-es",
    "fr": "aura-2-agathe-fr",
    "it": "aura-2-livia-it",
}

STT_PROVIDERS = ("assemblyai", "soniox", "deepgram")
LANGUAGE_ANNOTATION_STT_PROVIDERS = frozenset(STT_PROVIDERS)
TTS_PROVIDERS = ("speechify", "elevenlabs", "deepgram")
LLM_PROVIDERS = ("google", "openrouter")
LLM_DEFAULT_MODELS = {
    "google": "gemini-3.5-flash-lite",
    "openrouter": "google/gemini-2.5-flash",
}
ELEVENLABS_HTTP_ONLY_MODELS = frozenset({"eleven_v4_turbo"})


@dataclass(frozen=True)
class VoiceProviders:
    stt: str
    tts: str
    tts_voice: str | None
    elevenlabs_model: str
    llm: str
    llm_model: str


@dataclass(frozen=True)
class VoiceLanguage:
    locale: Locale
    soniox_hints: list[Language]
    soniox_language_identification: bool
    deepgram_stt_language: str
    speechify_model: str
    speechify_language: str | None
    speechify_voice: str
    deepgram_tts_voice: str


@dataclass
class _BufferedSpeechifyAudio:
    audio: bytes
    sample_rate: int
    num_channels: int
    context_id: str | None


def _trailing_silence_cut_range(
    audio: bytes,
    sample_rate: int,
    num_channels: int,
    trailing_silence_ms: int,
) -> tuple[int, int]:
    if sample_rate <= 0 or num_channels <= 0:
        return 0, 0

    samples_per_window = max(1, sample_rate // 100)
    window_bytes = samples_per_window * num_channels * 2
    silent_windows = 0
    for end in range(len(audio), window_bytes - 1, -window_bytes):
        window = audio[end - window_bytes : end]
        sample_count = len(window) // 2
        rms = sqrt(sum(sample[0] ** 2 for sample in iter_unpack("<h", window)) / sample_count)
        if rms >= 184:
            break
        silent_windows += 1

    windows_to_keep = ceil(max(0, trailing_silence_ms) / 10)
    bytes_to_trim = max(0, silent_windows - windows_to_keep) * window_bytes
    return len(audio) - silent_windows * window_bytes, bytes_to_trim


class SpeechifyVoiceAgentTTSService(SpeechifyHttpTTSService):
    def __init__(
        self,
        *,
        rate_percent: int,
        trailing_silence_ms: int = 120,
        **kwargs: Any,
    ) -> None:
        self._rate_percent = rate_percent
        self._trailing_silence_ms = trailing_silence_ms
        super().__init__(**kwargs)

    def _build_payload(self, text: str) -> dict[str, Any]:
        payload = super()._build_payload(text)
        if self._rate_percent != 0:
            payload["input"] = (
                f'<speak><prosody rate="{self._rate_percent:+d}%">{escape(text)}</prosody></speak>'
            )
        return payload

    async def run_tts(self, text: str, context_id: str) -> AsyncGenerator[Frame | None, None]:
        pending_frames: deque[_BufferedSpeechifyAudio | Frame | None] = deque()
        held_bytes = 0
        holdback_bytes: int | None = None
        first_audio = True
        cumulative_time_before = self._cumulative_time

        async for frame in super().run_tts(text, context_id):
            if not isinstance(frame, TTSAudioRawFrame):
                pending_frames.append(frame)
                while pending_frames and not isinstance(pending_frames[0], _BufferedSpeechifyAudio):
                    yield pending_frames.popleft()
                continue

            if frame.sample_rate <= 0 or frame.num_channels <= 0:
                pending_frames.append(frame)
                while pending_frames and not isinstance(pending_frames[0], _BufferedSpeechifyAudio):
                    yield pending_frames.popleft()
                continue

            if first_audio:
                first_audio = False
                holdback_bytes = frame.sample_rate * frame.num_channels * 2 * 500 // 1000
                if len(frame.audio) >= holdback_bytes:
                    yield frame
                    continue

            if holdback_bytes is None:
                holdback_bytes = frame.sample_rate * frame.num_channels * 2 * 500 // 1000

            pending_frames.append(
                _BufferedSpeechifyAudio(
                    audio=frame.audio,
                    sample_rate=frame.sample_rate,
                    num_channels=frame.num_channels,
                    context_id=frame.context_id,
                )
            )
            held_bytes += len(frame.audio)

            while held_bytes > holdback_bytes and pending_frames:
                oldest = pending_frames[0]
                if not isinstance(oldest, _BufferedSpeechifyAudio):
                    yield pending_frames.popleft()
                    continue

                bytes_to_emit = min(held_bytes - holdback_bytes, len(oldest.audio))
                alignment = oldest.num_channels * 2
                bytes_to_emit -= bytes_to_emit % alignment
                if bytes_to_emit == 0:
                    break

                audio = oldest.audio[:bytes_to_emit]
                oldest.audio = oldest.audio[bytes_to_emit:]
                held_bytes -= bytes_to_emit
                yield TTSAudioRawFrame(
                    audio=audio,
                    sample_rate=oldest.sample_rate,
                    num_channels=oldest.num_channels,
                    context_id=oldest.context_id,
                )
                if not oldest.audio:
                    pending_frames.popleft()

        trimmed_seconds = 0.0
        audio_chunks = [
            item for item in pending_frames if isinstance(item, _BufferedSpeechifyAudio)
        ]
        if audio_chunks:
            tail = b"".join(chunk.audio for chunk in audio_chunks)
            last_chunk = audio_chunks[-1]
            trim_start, bytes_to_trim = _trailing_silence_cut_range(
                tail,
                last_chunk.sample_rate,
                last_chunk.num_channels,
                self._trailing_silence_ms,
            )
            trim_end = trim_start + bytes_to_trim
            offset = 0
            for chunk in pending_frames:
                if not isinstance(chunk, _BufferedSpeechifyAudio):
                    continue

                chunk_length = len(chunk.audio)
                chunk_end = offset + chunk_length
                local_start = max(0, trim_start - offset)
                local_end = min(chunk_length, trim_end - offset)
                if local_start < local_end:
                    bytes_removed = local_end - local_start
                    chunk.audio = chunk.audio[:local_start] + chunk.audio[local_end:]
                    held_bytes -= bytes_removed
                    trimmed_seconds += bytes_removed / (chunk.sample_rate * chunk.num_channels * 2)
                offset = chunk_end
            pending_frames = deque(
                item
                for item in pending_frames
                if not isinstance(item, _BufferedSpeechifyAudio) or item.audio
            )

        if trimmed_seconds:
            self._cumulative_time = max(
                cumulative_time_before,
                self._cumulative_time - trimmed_seconds,
            )

        for chunk in pending_frames:
            if isinstance(chunk, _BufferedSpeechifyAudio):
                if not chunk.audio:
                    continue
                yield TTSAudioRawFrame(
                    audio=chunk.audio,
                    sample_rate=chunk.sample_rate,
                    num_channels=chunk.num_channels,
                    context_id=chunk.context_id,
                )
            else:
                yield chunk


def resolve_providers(env: Mapping[str, str]) -> VoiceProviders:
    stt = _provider_name(env, "STT_PROVIDER", "assemblyai", STT_PROVIDERS)
    tts = _provider_name(env, "TTS_PROVIDER", "speechify", TTS_PROVIDERS)
    llm = _provider_name(env, "LLM_PROVIDER", "google", LLM_PROVIDERS)
    tts_voice = env.get("TTS_VOICE", "").strip() or None
    elevenlabs_model = env.get("ELEVENLABS_MODEL", "eleven_v4_turbo").strip()
    default_llm_model = LLM_DEFAULT_MODELS[llm]
    llm_model = env.get("LLM_MODEL", default_llm_model).strip() or default_llm_model
    required_keys = ["GOOGLE_API_KEY" if llm == "google" else "OPENROUTER_API_KEY"]
    if stt == "assemblyai":
        required_keys.append("ASSEMBLYAI_API_KEY")
    elif stt == "soniox":
        required_keys.append("SONIOX_API_KEY")
    if "deepgram" in (stt, tts):
        required_keys.append("DEEPGRAM_API_KEY")
    if tts == "speechify":
        required_keys.append("SPEECHIFY_API_KEY")
    elif tts == "elevenlabs":
        required_keys.append("ELEVENLABS_API_KEY")
        if tts_voice is None:
            raise ValueError(
                "Missing required environment variable: TTS_VOICE (ElevenLabs voice ID)"
            )

    missing_keys = [name for name in required_keys if not env.get(name, "").strip()]
    if missing_keys:
        raise ValueError(f"Missing required environment variable(s): {', '.join(missing_keys)}")

    return VoiceProviders(
        stt=stt,
        tts=tts,
        tts_voice=tts_voice,
        elevenlabs_model=elevenlabs_model or "eleven_v4_turbo",
        llm=llm,
        llm_model=llm_model,
    )


def resolve_voice_language(
    config: AssistantConfig,
    providers: VoiceProviders,
    env: Mapping[str, str],
) -> VoiceLanguage:
    locale = config.language
    locale_info = LOCALES[locale]
    soniox_hints = (
        [Language(language) for language in MULTI_LANGUAGES]
        if locale == "multi"
        else [Language(locale_info.base)]
    )
    deepgram_language = {
        "en-GB": "en-GB",
        "en-US": "en-US",
        "pt-BR": "pt-BR",
        "multi": "multi",
    }.get(locale, locale_info.base)
    speechify_language = None if locale == "en-GB" else locale_info.speechify_language
    deepgram_voice = DEEPGRAM_TTS_VOICES.get(
        locale,
        DEEPGRAM_TTS_VOICES.get(locale_info.base, "aura-2-pandora-en"),
    )

    if providers.tts == "deepgram" and locale_info.base in {"pt", "multi"}:
        logger.warning(
            "Deepgram Aura-2 has no configured {} voice; using English fallback {}.",
            locale,
            deepgram_voice,
        )

    return VoiceLanguage(
        locale=locale,
        soniox_hints=soniox_hints,
        soniox_language_identification=locale == "multi",
        deepgram_stt_language=deepgram_language,
        speechify_model=locale_info.speechify_model,
        speechify_language=speechify_language,
        speechify_voice=(config.voice_id or providers.tts_voice or DEFAULT_VOICE_ID),
        deepgram_tts_voice=deepgram_voice,
    )


def create_stt(
    providers: VoiceProviders,
    env: Mapping[str, str],
    sample_rate: int | None = None,
    *,
    voice_language: VoiceLanguage | None = None,
    extra_terms: list[str] | None = None,
):
    voice_language = voice_language or _default_voice_language(providers, env)
    terms = merge_keyterms(extra_terms or [])
    provider_terms = terms or None
    if providers.stt == "assemblyai":
        return AssemblyAISTTService(
            api_key=env["ASSEMBLYAI_API_KEY"],
            sample_rate=sample_rate,
            settings=AssemblyAISTTService.Settings(
                model="universal-3-6-pro",
                keyterms_prompt=provider_terms,
            ),
        )
    if providers.stt == "soniox":
        return SonioxSTTService(
            api_key=env["SONIOX_API_KEY"],
            sample_rate=sample_rate,
            settings=SonioxSTTService.Settings(
                model="stt-rt-v5",
                language_hints=list(voice_language.soniox_hints),
                enable_language_identification=voice_language.soniox_language_identification,
                context=SonioxContextObject(terms=provider_terms),
            ),
        )
    if providers.stt == "deepgram":
        return DeepgramSTTService(
            api_key=env["DEEPGRAM_API_KEY"],
            sample_rate=sample_rate,
            settings=DeepgramSTTService.Settings(
                model="nova-3",
                language=voice_language.deepgram_stt_language,
                keyterm=provider_terms,
            ),
        )
    raise ValueError(f"Unsupported STT provider: {providers.stt}")


def create_tts(
    providers: VoiceProviders,
    env: Mapping[str, str],
    aiohttp_session: aiohttp.ClientSession,
    sample_rate: int | None = None,
    *,
    voice_language: VoiceLanguage | None = None,
    pronunciations: list[PronunciationEntry] | None = None,
    speed: float = 1.0,
):
    voice_language = voice_language or _default_voice_language(providers, env)
    text_transforms = [("*", PronunciationTransform(pronunciations))] if pronunciations else None
    if providers.tts == "speechify":
        voice_id = voice_language.speechify_voice
        rate_percent = speechify_rate_percent(voice_id, speed)
        measured_wpm = speechify_voice_wpm(voice_id)
        logger.info(
            "Speechify voice {} measured {} WPM; applied rate {:+d}%",
            voice_id,
            measured_wpm if measured_wpm is not None else "unknown",
            rate_percent,
        )
        settings = {
            "model": voice_language.speechify_model,
            "voice": voice_id,
        }
        if voice_language.speechify_language is not None:
            settings["language"] = voice_language.speechify_language
        service_kwargs = {
            "api_key": env["SPEECHIFY_API_KEY"],
            "aiohttp_session": aiohttp_session,
            "sample_rate": sample_rate,
            "text_transforms": text_transforms,
            "settings": SpeechifyHttpTTSService.Settings(**settings),
        }
        return SpeechifyVoiceAgentTTSService(rate_percent=rate_percent, **service_kwargs)
    if providers.tts == "elevenlabs":
        speed_setting = {"speed": speed} if speed != 1.0 else {}
        if providers.elevenlabs_model in ELEVENLABS_HTTP_ONLY_MODELS:
            return ElevenLabsHttpTTSService(
                api_key=env["ELEVENLABS_API_KEY"],
                aiohttp_session=aiohttp_session,
                sample_rate=sample_rate,
                text_transforms=text_transforms,
                settings=ElevenLabsHttpTTSService.Settings(
                    model=providers.elevenlabs_model,
                    voice=providers.tts_voice,
                    **speed_setting,
                ),
            )
        return ElevenLabsTTSService(
            api_key=env["ELEVENLABS_API_KEY"],
            sample_rate=sample_rate,
            text_transforms=text_transforms,
            settings=ElevenLabsTTSService.Settings(
                model=providers.elevenlabs_model,
                voice=providers.tts_voice,
                **speed_setting,
            ),
        )
    if providers.tts == "deepgram":
        speed_setting = {"speed": speed} if speed != 1.0 else {}
        return DeepgramTTSService(
            api_key=env["DEEPGRAM_API_KEY"],
            sample_rate=sample_rate,
            text_transforms=text_transforms,
            settings=DeepgramTTSService.Settings(
                voice=providers.tts_voice or voice_language.deepgram_tts_voice,
                **speed_setting,
            ),
        )
    raise ValueError(f"Unsupported TTS provider: {providers.tts}")


def merge_keyterms(extra_terms: list[str]) -> list[str]:
    terms: list[str] = []
    seen: set[str] = set()
    for term in extra_terms:
        normalized = term.strip()
        key = normalized.casefold()
        if normalized and key not in seen:
            terms.append(normalized)
            seen.add(key)
    return terms


def _default_voice_language(
    providers: VoiceProviders,
    env: Mapping[str, str],
) -> VoiceLanguage:
    config = AssistantConfig.model_construct(language=DEFAULT_LOCALE)
    return resolve_voice_language(config, providers, env)


def create_llm(
    providers: VoiceProviders,
    env: Mapping[str, str],
    system_instruction: str,
):
    if providers.llm == "google":
        if providers.llm_model.startswith("gemini-2.5"):
            thinking = GoogleThinkingConfig(thinking_budget=0)
        else:
            thinking_level = env.get("LLM_THINKING_LEVEL", "minimal").strip() or "minimal"
            thinking = GoogleThinkingConfig(thinking_level=thinking_level)
        return GoogleLLMService(
            api_key=env["GOOGLE_API_KEY"],
            settings=GoogleLLMService.Settings(
                model=providers.llm_model,
                system_instruction=system_instruction,
                temperature=0.2,
                thinking=thinking,
            ),
        )
    if providers.llm == "openrouter":
        return OpenRouterLLMService(
            api_key=env["OPENROUTER_API_KEY"],
            settings=OpenRouterLLMService.Settings(
                model=providers.llm_model,
                system_instruction=system_instruction,
                temperature=0.2,
            ),
        )
    raise ValueError(f"Unsupported LLM provider: {providers.llm}")


def _provider_name(
    env: Mapping[str, str],
    variable: str,
    default: str,
    allowed: tuple[str, ...],
) -> str:
    name = env.get(variable, default).strip().lower()
    if name not in allowed:
        choices = ", ".join(allowed)
        raise ValueError(f"Unknown {variable} {name!r}. Allowed providers: {choices}.")
    return name
