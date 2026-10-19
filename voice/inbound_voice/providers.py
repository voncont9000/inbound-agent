from collections.abc import Mapping
from dataclasses import dataclass
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
from loguru import logger
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


class SpeechifyRateTTSService(SpeechifyHttpTTSService):
    def __init__(self, *, speed: float, **kwargs: Any) -> None:
        self._rate_percent = round((speed - 1) * 100)
        super().__init__(**kwargs)

    def _build_payload(self, text: str) -> dict[str, Any]:
        payload = super()._build_payload(text)
        payload["input"] = (
            f'<speak><prosody rate="{self._rate_percent:+d}%">{escape(text)}</prosody></speak>'
        )
        return payload


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
        settings = {
            "model": voice_language.speechify_model,
            "voice": voice_language.speechify_voice,
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
        if round((speed - 1) * 100) == 0:
            return SpeechifyHttpTTSService(**service_kwargs)
        return SpeechifyRateTTSService(speed=speed, **service_kwargs)
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
