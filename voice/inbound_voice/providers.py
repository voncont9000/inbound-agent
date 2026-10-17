from collections.abc import Mapping
from dataclasses import dataclass

import aiohttp
from pipecat.services.assemblyai.stt import AssemblyAISTTService
from pipecat.services.deepgram.stt import DeepgramSTTService
from pipecat.services.deepgram.tts import DeepgramTTSService
from pipecat.services.elevenlabs.tts import ElevenLabsHttpTTSService, ElevenLabsTTSService
from pipecat.services.google.llm import GoogleLLMService, GoogleThinkingConfig
from pipecat.services.openrouter.llm import OpenRouterLLMService
from pipecat.services.soniox.stt import SonioxContextObject, SonioxSTTService
from pipecat.services.speechify.tts import SpeechifyHttpTTSService
from pipecat.transcriptions.language import Language

STT_PROVIDERS = ("assemblyai", "soniox", "deepgram")
TTS_PROVIDERS = ("speechify", "elevenlabs", "deepgram")
LLM_PROVIDERS = ("google", "openrouter")
LLM_DEFAULT_MODELS = {
    "google": "gemini-2.5-flash",
    "openrouter": "google/gemini-2.5-flash",
}
ELEVENLABS_HTTP_ONLY_MODELS = frozenset({"eleven_v4_turbo"})

KEYTERMS = [
    "conveyancing",
    "freehold",
    "leasehold",
    "ACAS",
    "tribunal",
    "remortgage",
    "completion",
    "exchange",
    "solicitor",
    "redundancy",
    "settlement agreement",
]


@dataclass(frozen=True)
class VoiceProviders:
    stt: str
    tts: str
    tts_voice: str | None
    elevenlabs_model: str
    llm: str
    llm_model: str


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


def create_stt(providers: VoiceProviders, env: Mapping[str, str]):
    if providers.stt == "assemblyai":
        return AssemblyAISTTService(
            api_key=env["ASSEMBLYAI_API_KEY"],
            settings=AssemblyAISTTService.Settings(
                model="universal-3-6-pro",
                keyterms_prompt=KEYTERMS,
            ),
        )
    if providers.stt == "soniox":
        return SonioxSTTService(
            api_key=env["SONIOX_API_KEY"],
            settings=SonioxSTTService.Settings(
                model="stt-rt-v5",
                language_hints=[Language.EN],
                context=SonioxContextObject(terms=KEYTERMS),
            ),
        )
    if providers.stt == "deepgram":
        return DeepgramSTTService(
            api_key=env["DEEPGRAM_API_KEY"],
            settings=DeepgramSTTService.Settings(
                model="nova-3",
                language="en-GB",
                keyterm=KEYTERMS,
            ),
        )
    raise ValueError(f"Unsupported STT provider: {providers.stt}")


def create_tts(
    providers: VoiceProviders,
    env: Mapping[str, str],
    aiohttp_session: aiohttp.ClientSession,
):
    if providers.tts == "speechify":
        return SpeechifyHttpTTSService(
            api_key=env["SPEECHIFY_API_KEY"],
            aiohttp_session=aiohttp_session,
            settings=SpeechifyHttpTTSService.Settings(
                model="simba-3.2",
                voice=providers.tts_voice or "beatrice_32",
            ),
        )
    if providers.tts == "elevenlabs":
        if providers.elevenlabs_model in ELEVENLABS_HTTP_ONLY_MODELS:
            return ElevenLabsHttpTTSService(
                api_key=env["ELEVENLABS_API_KEY"],
                aiohttp_session=aiohttp_session,
                settings=ElevenLabsHttpTTSService.Settings(
                    model=providers.elevenlabs_model,
                    voice=providers.tts_voice,
                ),
            )
        return ElevenLabsTTSService(
            api_key=env["ELEVENLABS_API_KEY"],
            settings=ElevenLabsTTSService.Settings(
                model=providers.elevenlabs_model,
                voice=providers.tts_voice,
            ),
        )
    if providers.tts == "deepgram":
        return DeepgramTTSService(
            api_key=env["DEEPGRAM_API_KEY"],
            settings=DeepgramTTSService.Settings(
                voice=providers.tts_voice or "aura-2-pandora-en",
            ),
        )
    raise ValueError(f"Unsupported TTS provider: {providers.tts}")


def create_llm(
    providers: VoiceProviders,
    env: Mapping[str, str],
    system_instruction: str,
):
    if providers.llm == "google":
        return GoogleLLMService(
            api_key=env["GOOGLE_API_KEY"],
            settings=GoogleLLMService.Settings(
                model=providers.llm_model,
                system_instruction=system_instruction,
                temperature=0.2,
                thinking=GoogleThinkingConfig(thinking_budget=0),
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
