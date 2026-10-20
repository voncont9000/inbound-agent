import asyncio
from io import StringIO
from pathlib import Path

import aiohttp
import pytest
from inbound_core.config import AssistantConfig, PronunciationEntry
from loguru import logger
from pipecat.frames.frames import (
    AggregatedTextFrame,
    AggregationType,
    Frame,
    TTSAudioRawFrame,
    TTSTextFrame,
)
from pipecat.services.assemblyai.stt import AssemblyAISTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService, ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.services.soniox.stt import SonioxSTTService
from pipecat.services.speechify.tts import SpeechifyHttpTTSService, _SpeechMarkAccumulator

from inbound_voice.pronunciation import PronunciationTransform
from inbound_voice.providers import (
    SpeechifyVoiceAgentTTSService,
    VoiceProviders,
    create_llm,
    create_stt,
    create_tts,
    resolve_providers,
    resolve_voice_language,
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
    terms = ["Acme Clinic", "Northstar"]

    assert isinstance(service, expected_type)
    if provider == "assemblyai":
        assert service._vad_force_turn_endpoint is True
        assert service._settings.model == "universal-3-6-pro"
        assert service._settings.keyterms_prompt == terms
    elif provider == "soniox":
        assert service._vad_force_turn_endpoint is True
        assert service._settings.model == "stt-rt-v5"
        assert service._settings.language_hints == ["en"]
        assert service._settings.enable_language_identification is False
        assert service._settings.context.terms == terms
    else:
        assert service._settings.model == "nova-3"
        assert service._settings.language == "en-GB"
        assert service._settings.keyterm == terms


@pytest.mark.parametrize(
    ("provider", "expected_language"),
    [("soniox", ["de"]), ("deepgram", "de")],
)
def test_stt_combines_locale_language_and_extra_terms(
    provider: str, expected_language: str | list[str]
) -> None:
    environment = _environment(STT_PROVIDER=provider)
    providers = resolve_providers(environment)
    config = AssistantConfig.model_construct(language="de-DE")
    voice_language = resolve_voice_language(config, providers, environment)
    service = create_stt(
        providers,
        environment,
        voice_language=voice_language,
        extra_terms=[" Acme Clinic ", "acme clinic", "Northstar"],
    )
    terms = ["Acme Clinic", "Northstar"]

    if provider == "soniox":
        assert service._settings.language_hints == expected_language
        assert service._settings.context.terms == terms
    else:
        assert service._settings.language == expected_language
        assert service._settings.keyterm == terms


@pytest.mark.parametrize(
    ("provider", "expected_type"),
    [
        ("assemblyai", AssemblyAISTTService),
        ("soniox", SonioxSTTService),
        ("deepgram", DeepgramSTTService),
    ],
)
def test_create_stt_omits_empty_configured_terms(provider: str, expected_type: type) -> None:
    environment = _environment(STT_PROVIDER=provider)
    service = create_stt(resolve_providers(environment), environment, extra_terms=[])

    assert isinstance(service, expected_type)
    if provider == "assemblyai":
        assert service._settings.keyterms_prompt is None
    elif provider == "soniox":
        assert service._settings.context.terms is None
    else:
        assert service._settings.keyterm is None


@pytest.mark.parametrize(
    (
        "locale",
        "soniox_hints",
        "deepgram_language",
        "speechify_model",
        "speechify_language",
        "deepgram_voice",
    ),
    [
        ("en-GB", ["en"], "en-GB", "simba-3.2", None, "aura-2-pandora-en"),
        ("en-US", ["en"], "en-US", "simba-3.2", "en-US", "aura-2-pandora-en"),
        ("de-DE", ["de"], "de", "simba-3.0", "de-DE", "aura-2-julius-de"),
        ("es-ES", ["es"], "es", "simba-3.0", "es-ES", "aura-2-nestor-es"),
        ("es-MX", ["es"], "es", "simba-3.0", "es-MX", "aura-2-estrella-es"),
        ("fr-FR", ["fr"], "fr", "simba-3.0", "fr-FR", "aura-2-agathe-fr"),
        ("it-IT", ["it"], "it", "simba-3.0", "it-IT", "aura-2-livia-it"),
        ("pt-BR", ["pt"], "pt-BR", "simba-3.0", "pt-BR", "aura-2-pandora-en"),
        (
            "multi",
            ["en", "de", "es", "fr", "it", "pt"],
            "multi",
            "simba-3.0",
            None,
            "aura-2-pandora-en",
        ),
    ],
)
def test_voice_language_resolves_provider_settings(
    locale: str,
    soniox_hints: list[str],
    deepgram_language: str,
    speechify_model: str,
    speechify_language: str | None,
    deepgram_voice: str,
) -> None:
    environment = _environment(STT_PROVIDER="soniox")
    providers = resolve_providers(environment)
    config = AssistantConfig.model_construct(language=locale)

    language = resolve_voice_language(config, providers, environment)
    stt = create_stt(providers, environment, voice_language=language)

    assert [hint.value for hint in language.soniox_hints] == soniox_hints
    assert language.soniox_language_identification is (locale == "multi")
    assert language.deepgram_stt_language == deepgram_language
    assert language.speechify_model == speechify_model
    assert language.speechify_language == speechify_language
    assert stt._settings.language_hints == soniox_hints
    assert stt._settings.enable_language_identification is (locale == "multi")

    assert language.deepgram_tts_voice == deepgram_voice


def test_voice_id_precedence_and_localized_speechify_settings() -> None:
    async def create_service() -> SpeechifyHttpTTSService:
        environment = _environment(TTS_VOICE="environment-voice")
        providers = resolve_providers(environment)
        config = AssistantConfig.model_construct(language="de-DE", voice_id="configured-voice")
        voice_language = resolve_voice_language(config, providers, environment)
        async with aiohttp.ClientSession() as session:
            return create_tts(providers, environment, session, voice_language=voice_language)

    service = asyncio.run(create_service())

    assert service._settings.model == "simba-3.0"
    assert service._settings.language == "de-DE"
    assert service._settings.voice == "configured-voice"


@pytest.mark.parametrize(
    ("config_voice", "environment_voice", "expected_voice"),
    [
        ("configured-voice", "environment-voice", "configured-voice"),
        (None, "environment-voice", "environment-voice"),
        (None, "", "beatrice_32"),
    ],
)
def test_speechify_voice_id_precedence(
    config_voice: str | None,
    environment_voice: str,
    expected_voice: str,
) -> None:
    environment = _environment(TTS_VOICE=environment_voice)
    providers = resolve_providers(environment)
    config = AssistantConfig.model_construct(language="de-DE", voice_id=config_voice)

    assert resolve_voice_language(config, providers, environment).speechify_voice == expected_voice


def test_prompt_stt_and_tts_share_the_assistant_locale() -> None:
    from inbound_voice.config import load_assistant_config
    from inbound_voice.prompts import render_system_prompt

    config_path = Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml"
    assistant = load_assistant_config(config_path)
    assistant.language = "de-DE"
    environment = _environment(STT_PROVIDER="soniox")
    providers = resolve_providers(environment)
    voice_language = resolve_voice_language(assistant, providers, environment)
    stt = create_stt(providers, environment, voice_language=voice_language)

    async def create_speechify() -> SpeechifyVoiceAgentTTSService:
        async with aiohttp.ClientSession() as session:
            return create_tts(providers, environment, session, voice_language=voice_language)

    tts = asyncio.run(create_speechify())
    prompt = render_system_prompt(assistant)

    assert "Speak German, even if the instructions" in prompt
    assert stt._settings.language_hints == ["de"]
    assert tts._settings.language == "de-DE"


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

    assert isinstance(speechify, SpeechifyVoiceAgentTTSService)
    assert speechify._settings.model == "simba-3.2"
    assert speechify._settings.voice == "beatrice_32"
    assert speechify._rate_percent == 6
    assert not hasattr(speechify._settings, "speed")
    assert speechify._settings.language is None
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


def test_speechify_payload_only_wraps_nonzero_rate_and_escapes_text() -> None:
    async def create_services() -> tuple[SpeechifyVoiceAgentTTSService, ...]:
        async with aiohttp.ClientSession() as session:
            return tuple(
                SpeechifyVoiceAgentTTSService(
                    rate_percent=rate_percent,
                    api_key="test-speechify-key",
                    aiohttp_session=session,
                    settings=SpeechifyHttpTTSService.Settings(
                        model="simba-3.2",
                        voice="alec",
                    ),
                )
                for rate_percent in (0, 29)
            )

    unchanged, adjusted = asyncio.run(create_services())
    text = "Hello & <team>"

    assert unchanged._build_payload(text)["input"] == text
    assert (
        adjusted._build_payload(text)["input"]
        == '<speak><prosody rate="+29%">Hello &amp; &lt;team&gt;</prosody></speak>'
    )


def test_speechify_create_tts_uses_measured_voice_pace_and_logs_it() -> None:
    async def create_service() -> SpeechifyVoiceAgentTTSService:
        environment = _environment()
        providers = resolve_providers(environment)
        voice_language = resolve_voice_language(
            AssistantConfig.model_construct(language="en-GB", voice_id="alec"),
            providers,
            environment,
        )
        async with aiohttp.ClientSession() as session:
            return create_tts(
                providers,
                environment,
                session,
                voice_language=voice_language,
                speed=1.2,
            )

    output = StringIO()
    sink_id = logger.add(output, level="INFO", format="{message}")
    try:
        service = asyncio.run(create_service())
    finally:
        logger.remove(sink_id)

    assert service._rate_percent == 54
    assert output.getvalue().count("Speechify voice alec measured 136 WPM; applied rate +54%") == 1


def _speechify_audio(
    duration_ms: int,
    *,
    amplitude: int = 1000,
    context_id: str = "context-1",
) -> TTSAudioRawFrame:
    sample_count = 24_000 * duration_ms // 1000
    sample = amplitude.to_bytes(2, byteorder="little", signed=True)
    return TTSAudioRawFrame(
        audio=sample * sample_count,
        sample_rate=24_000,
        num_channels=1,
        context_id=context_id,
    )


async def _speechify_service() -> SpeechifyVoiceAgentTTSService:
    async with aiohttp.ClientSession() as session:
        return SpeechifyVoiceAgentTTSService(
            rate_percent=29,
            api_key="test-speechify-key",
            aiohttp_session=session,
            sample_rate=24_000,
        )


def test_speechify_trims_trailing_silence_and_streams_before_completion(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    non_audio_frames = [Frame(), Frame()]
    state = {"finished": False}

    async def fake_run_tts(self, text: str, context_id: str):
        yield _speechify_audio(300, context_id=context_id)
        yield non_audio_frames[0]
        yield _speechify_audio(100, context_id=context_id)
        yield non_audio_frames[1]
        yield _speechify_audio(400, amplitude=0, context_id=context_id)
        self._cumulative_time += 0.8
        state["finished"] = True

    monkeypatch.setattr(SpeechifyHttpTTSService, "run_tts", fake_run_tts)

    async def collect():
        service = await _speechify_service()
        frames = []
        first_audio_before_completion = False
        async for frame in service.run_tts("Hello", "context-1"):
            frames.append(frame)
            if isinstance(frame, TTSAudioRawFrame) and not state["finished"]:
                first_audio_before_completion = True
        return service, frames, first_audio_before_completion

    service, frames, first_audio_before_completion = asyncio.run(collect())
    audio_frames = [frame for frame in frames if isinstance(frame, TTSAudioRawFrame)]
    non_audio = [frame for frame in frames if not isinstance(frame, TTSAudioRawFrame)]
    expected_audio = (
        _speechify_audio(300).audio
        + _speechify_audio(100).audio
        + _speechify_audio(120, amplitude=0).audio
    )

    assert first_audio_before_completion
    assert isinstance(frames[0], TTSAudioRawFrame)
    assert frames[1] is non_audio_frames[0]
    assert isinstance(frames[2], TTSAudioRawFrame)
    assert frames[3] is non_audio_frames[1]
    assert isinstance(frames[4], TTSAudioRawFrame)
    assert [id(frame) for frame in non_audio] == [id(frame) for frame in non_audio_frames]
    assert b"".join(frame.audio for frame in audio_frames) == expected_audio
    assert sum(len(frame.audio) for frame in audio_frames) * 1000 / 48_000 == 520
    assert all(frame.sample_rate == 24_000 for frame in audio_frames)
    assert all(frame.num_channels == 1 for frame in audio_frames)
    assert all(frame.context_id == "context-1" for frame in audio_frames)
    assert service._cumulative_time == pytest.approx(0.52)


def test_speechify_trims_trailing_silence_from_single_large_chunk(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tone = _speechify_audio(600).audio
    silence = _speechify_audio(300, amplitude=0).audio
    source_frame = TTSAudioRawFrame(
        audio=tone + silence,
        sample_rate=24_000,
        num_channels=1,
        context_id="context-1",
    )
    state = {"finished": False}

    async def fake_run_tts(self, text: str, context_id: str):
        yield source_frame
        state["finished"] = True

    monkeypatch.setattr(SpeechifyHttpTTSService, "run_tts", fake_run_tts)

    async def collect():
        service = await _speechify_service()
        frames = []
        first_audio_before_completion = False
        async for frame in service.run_tts("Hello", "context-1"):
            frames.append(frame)
            if isinstance(frame, TTSAudioRawFrame) and not state["finished"]:
                first_audio_before_completion = True
        return frames, first_audio_before_completion

    frames, first_audio_before_completion = asyncio.run(collect())
    audio_frames = [frame for frame in frames if isinstance(frame, TTSAudioRawFrame)]

    assert first_audio_before_completion
    assert b"".join(frame.audio for frame in audio_frames) == (
        tone + _speechify_audio(120, amplitude=0).audio
    )
    assert sum(len(frame.audio) for frame in audio_frames) * 1000 / 48_000 == 720


def test_speechify_all_speech_audio_is_unchanged(monkeypatch: pytest.MonkeyPatch) -> None:
    source_frames = [_speechify_audio(300), _speechify_audio(400)]

    async def fake_run_tts(self, text: str, context_id: str):
        for frame in source_frames:
            yield TTSAudioRawFrame(
                audio=frame.audio,
                sample_rate=frame.sample_rate,
                num_channels=frame.num_channels,
                context_id=context_id,
            )

    async def collect():
        service = await _speechify_service()
        frames = [frame async for frame in service.run_tts("Hello", "context-1")]
        return frames

    monkeypatch.setattr(SpeechifyHttpTTSService, "run_tts", fake_run_tts)
    frames = asyncio.run(collect())
    audio_frames = [frame for frame in frames if isinstance(frame, TTSAudioRawFrame)]

    assert b"".join(frame.audio for frame in audio_frames) == b"".join(
        frame.audio for frame in source_frames
    )


def test_speechify_first_large_chunk_is_emitted_immediately(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    state = {"finished": False}

    async def fake_run_tts(self, text: str, context_id: str):
        yield _speechify_audio(600, context_id=context_id)
        yield _speechify_audio(100, amplitude=0, context_id=context_id)
        state["finished"] = True

    async def collect():
        service = await _speechify_service()
        first_audio_before_completion = False
        async for frame in service.run_tts("Hello", "context-1"):
            if isinstance(frame, TTSAudioRawFrame) and not state["finished"]:
                first_audio_before_completion = True
        return first_audio_before_completion

    monkeypatch.setattr(SpeechifyHttpTTSService, "run_tts", fake_run_tts)
    assert asyncio.run(collect())


def test_speechify_speech_marks_map_to_plain_spoken_text() -> None:
    text = "Hello & welcome."
    marks = [
        {"type": "word", "value": "Hello", "start": 0, "end": 5, "start_time": 0, "end_time": 300},
        {"type": "word", "value": "&", "start": 6, "end": 7, "start_time": 400, "end_time": 500},
        {
            "type": "word",
            "value": "welcome.",
            "start": 8,
            "end": 16,
            "start_time": 600,
            "end_time": 900,
        },
    ]

    words = _SpeechMarkAccumulator(text).add(marks)

    assert [word for word, _ in words] == ["Hello", "&", "welcome."]
    assert all("<speak>" not in word and "<prosody" not in word for word, _ in words)


@pytest.mark.parametrize(
    ("provider", "model"),
    [
        ("speechify", None),
        ("elevenlabs", "eleven_v4_turbo"),
        ("elevenlabs", "eleven_flash_v2_5"),
        ("deepgram", None),
    ],
)
def test_create_tts_attaches_pronunciation_transform(provider: str, model: str | None) -> None:
    async def create_service():
        async with aiohttp.ClientSession() as session:
            environment = _environment(TTS_PROVIDER=provider)
            if provider == "elevenlabs":
                environment["TTS_VOICE"] = "elevenlabs-voice-id"
                environment["ELEVENLABS_MODEL"] = model or "eleven_v4_turbo"
            return create_tts(
                resolve_providers(environment),
                environment,
                session,
                pronunciations=[PronunciationEntry(term="ACAS", say_as="Ay-cass")],
            )

    service = asyncio.run(create_service())

    assert len(service._text_transforms) == 1
    aggregation_type, transform = service._text_transforms[0]
    assert aggregation_type == "*"
    assert isinstance(transform, PronunciationTransform)


def test_deepgram_keeps_original_text_in_context_frame() -> None:
    async def exercise() -> list[object]:
        async with aiohttp.ClientSession() as session:
            environment = _environment(TTS_PROVIDER="deepgram")
            service = create_tts(
                resolve_providers(environment),
                environment,
                session,
                pronunciations=[PronunciationEntry(term="ACAS", say_as="Ay-cass")],
            )

        frames = []

        async def record_frame(context_id, frame):
            frames.append(frame)

        async def run_tts(text, context_id):
            assert text == "Ay-cass "
            if False:
                yield None

        async def no_op(*args, **kwargs):
            pass

        service.append_to_audio_context = record_frame
        service.run_tts = run_tts
        service.create_audio_context = no_op
        service.start_ttfb_metrics = no_op
        service.start_processing_metrics = no_op
        service.stop_processing_metrics = no_op
        service.push_frame = no_op
        assert service._push_text_frames is True
        await service._push_tts_frames(
            AggregatedTextFrame("ACAS", aggregated_by=AggregationType.SENTENCE)
        )
        return frames

    text_frames = [frame for frame in asyncio.run(exercise()) if isinstance(frame, TTSTextFrame)]

    assert len(text_frames) == 1
    assert text_frames[0].text == "ACAS"


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
