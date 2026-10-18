import asyncio

import aiohttp
import pytest
from pipecat.services.assemblyai.stt import AssemblyAISTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService, ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.services.soniox.stt import SonioxSTTService
from pipecat.services.speechify.tts import SpeechifyHttpTTSService

from inbound_voice.providers import (
    KEYTERMS,
    VoiceProviders,
    create_llm,
    create_stt,
    create_tts,
    resolve_providers,
)


def _environment(**overrides: str) -> dict[str, str]:
    return {
        "GOOGLE_API_KEY": "test-google-key",
        "OPENROUTER_API_KEY": "test-openrouter-key",
        "ASSEMBLYAI_API_KEY": "test-assemblyai-key",
        "SONIOX_API_KEY": "test-soniox-key",
        "DEEPGRAM_API_KEY": "test-deepgram-key",
        "SPEECHIFY_API_KEY": "test-speechify-key",
        "ELEVENLABS_API_KEY": "test-elevenlabs-key",
        **overrides,
    }


def test_provider_defaults_and_normalization() -> None:
    providers = resolve_providers(_environment())

    assert providers == VoiceProviders(
        stt="assemblyai",
        tts="speechify",
        tts_voice=None,
        elevenlabs_model="eleven_v4_turbo",
        llm="google",
        llm_model="gemini-3.5-flash-lite",
    )
    normalized = resolve_providers(
        _environment(STT_PROVIDER=" SONIOX ", TTS_PROVIDER=" DEEPGRAM ", TTS_VOICE=" voice ")
    )
    assert normalized.stt == "soniox"
    assert normalized.tts == "deepgram"
    assert normalized.tts_voice == "voice"


@pytest.mark.parametrize(
    ("variable", "overrides"),
    [
        ("GOOGLE_API_KEY", {}),
        ("OPENROUTER_API_KEY", {"LLM_PROVIDER": "openrouter"}),
        ("ASSEMBLYAI_API_KEY", {}),
        ("SONIOX_API_KEY", {"STT_PROVIDER": "soniox"}),
        ("DEEPGRAM_API_KEY", {"STT_PROVIDER": "deepgram"}),
        ("DEEPGRAM_API_KEY", {"TTS_PROVIDER": "deepgram"}),
        ("SPEECHIFY_API_KEY", {}),
        ("ELEVENLABS_API_KEY", {"TTS_PROVIDER": "elevenlabs", "TTS_VOICE": "voice-id"}),
    ],
)
def test_selected_provider_requires_api_key(variable: str, overrides: dict[str, str]) -> None:
    environment = _environment(**overrides)
    del environment[variable]

    with pytest.raises(ValueError, match=variable):
        resolve_providers(environment)


def test_unknown_provider_reports_allowed_values() -> None:
    with pytest.raises(ValueError, match="assemblyai, soniox, deepgram"):
        resolve_providers(_environment(STT_PROVIDER="unknown"))


def test_openrouter_provider_uses_its_default_model() -> None:
    providers = resolve_providers(_environment(LLM_PROVIDER="openrouter"))

    assert providers.llm == "openrouter"
    assert providers.llm_model == "google/gemini-2.5-flash"


def test_unknown_llm_provider_reports_allowed_values() -> None:
    with pytest.raises(ValueError, match="google, openrouter"):
        resolve_providers(_environment(LLM_PROVIDER="unknown"))


def test_elevenlabs_requires_voice_id() -> None:
    with pytest.raises(ValueError, match="TTS_VOICE"):
        resolve_providers(_environment(TTS_PROVIDER="elevenlabs"))


@pytest.mark.parametrize(
    ("provider", "expected_type"),
    [
        ("assemblyai", AssemblyAISTTService),
        ("soniox", SonioxSTTService),
        ("deepgram", DeepgramSTTService),
    ],
)
def test_create_stt(provider: str, expected_type: type) -> None:
    environment = _environment(STT_PROVIDER=provider)
    service = create_stt(
        resolve_providers(environment),
        environment,
        extra_terms=[" Acme Clinic ", "acme clinic", "Northstar"],
    )
    terms = [*KEYTERMS, "Acme Clinic", "Northstar"]

    assert isinstance(service, expected_type)
    if provider == "assemblyai":
        assert service._vad_force_turn_endpoint is True
        assert service._settings.model == "universal-3-6-pro"
        assert service._settings.keyterms_prompt == terms
    elif provider == "soniox":
        assert service._vad_force_turn_endpoint is True
        assert service._settings.model == "stt-rt-v5"
        assert service._settings.language_hints == ["en"]
        assert service._settings.context.terms == terms
    else:
        assert service._settings.model == "nova-3"
        assert service._settings.language == "en-GB"
        assert service._settings.keyterm == terms


def test_create_tts() -> None:
    async def create_services() -> tuple[object, object, object, object, object]:
        async with aiohttp.ClientSession() as session:
            services = []
            for provider, voice, model, speed in (
                ("speechify", None, None, 0.9),
                ("elevenlabs", "elevenlabs-voice-id", None, 1.1),
                ("deepgram", None, None, 1.2),
                ("elevenlabs", "elevenlabs-voice-id", "eleven_flash_v2_5", 1.15),
                ("deepgram", None, None, 1.0),
            ):
                environment = _environment(TTS_PROVIDER=provider)
                if voice:
                    environment["TTS_VOICE"] = voice
                if model:
                    environment["ELEVENLABS_MODEL"] = model
                providers = resolve_providers(environment)
                services.append(create_tts(providers, environment, session, speed=speed))
            return tuple(services)

    speechify, elevenlabs, deepgram, elevenlabs_flash, deepgram_default = asyncio.run(
        create_services()
    )

    assert isinstance(speechify, SpeechifyHttpTTSService)
    assert speechify._settings.model == "simba-3.2"
    assert speechify._settings.voice == "beatrice_32"
    assert not hasattr(speechify._settings, "speed")
    assert isinstance(elevenlabs, ElevenLabsHttpTTSService)
    assert elevenlabs._settings.model == "eleven_v4_turbo"
    assert elevenlabs._settings.voice == "elevenlabs-voice-id"
    assert elevenlabs._settings.speed == 1.1
    assert isinstance(elevenlabs_flash, ElevenLabsTTSService)
    assert elevenlabs_flash._settings.model == "eleven_flash_v2_5"
    assert elevenlabs_flash._settings.voice == "elevenlabs-voice-id"
    assert elevenlabs_flash._settings.speed == 1.15
    assert isinstance(deepgram, DeepgramTTSService)
    assert deepgram._settings.voice == "aura-2-pandora-en"
    assert deepgram._settings.speed == 1.2
    assert deepgram_default._settings.speed is None


def test_create_llm() -> None:
    google_env = _environment()
    google = create_llm(resolve_providers(google_env), google_env, "system prompt")

    assert isinstance(google, GoogleLLMService)
    assert google._settings.model == "gemini-3.5-flash-lite"
    assert google._settings.system_instruction == "system prompt"
    assert google._settings.thinking.thinking_level == "minimal"

    legacy_google_env = _environment(LLM_MODEL="gemini-2.5-flash")
    legacy_google = create_llm(
        resolve_providers(legacy_google_env), legacy_google_env, "system prompt"
    )
    assert legacy_google._settings.thinking.thinking_budget == 0

    openrouter_env = _environment(LLM_PROVIDER="openrouter")
    openrouter = create_llm(resolve_providers(openrouter_env), openrouter_env, "system prompt")

    assert isinstance(openrouter, OpenRouterLLMService)
    assert openrouter._settings.model == "google/gemini-2.5-flash"
    assert openrouter._settings.system_instruction == "system prompt"
