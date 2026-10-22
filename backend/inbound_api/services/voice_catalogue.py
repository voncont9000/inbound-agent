import logging
import time
from collections.abc import Callable
from typing import Any

import httpx
from inbound_core.languages import (
    DEFAULT_LOCALE,
    DEFAULT_VOICE_ID,
    LOCALES,
    MULTI_LANGUAGES,
    Locale,
)
from inbound_core.voice_pace import speechify_voice_pauses_mid_sentence

from inbound_api.schemas import (
    VoiceCard,
    VoiceCatalogueResponse,
    VoiceLocaleOption,
)
from inbound_api.settings import get_settings

SPEECHIFY_VOICES_URL = "https://api.speechify.ai/v1/voices"
CACHE_TTL_SECONDS = 24 * 60 * 60
logger = logging.getLogger(__name__)


class SpeechifyVoiceCatalogue:
    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        self._clock = clock
        self._transport = transport
        self._cache: dict[tuple[str, Locale], tuple[float, tuple[VoiceCard, ...]]] = {}

    async def get(self, locale: Locale, api_key: str | None) -> VoiceCatalogueResponse:
        model = LOCALES[locale].speechify_model
        if not api_key:
            return self._response(locale, [self._default_voice()])

        cache_key = (model, locale)
        cached = self._cache.get(cache_key)
        if cached is not None and self._clock() < cached[0]:
            return self._response(locale, list(cached[1]))

        try:
            provider_voices = await self._fetch_voices(api_key, locale, model)
            voices = self._map_voices(provider_voices, locale, model)
        except Exception as error:
            logger.warning(
                "Speechify voice catalogue request failed (%s); returning the default voice.",
                type(error).__name__,
            )
            return self._response(locale, [self._default_voice()])

        self._cache[cache_key] = (self._clock() + CACHE_TTL_SECONDS, tuple(voices))
        return self._response(locale, voices)

    async def _fetch_voices(self, api_key: str, locale: Locale, model: str) -> list[dict[str, Any]]:
        params: dict[str, str | int] = {
            "type": "shared",
            "model": model,
            "limit": 200,
        }
        if locale != "multi":
            params["locale"] = LOCALES[locale].base

        voices: list[dict[str, Any]] = []
        seen_cursors: set[str] = set()
        async with httpx.AsyncClient(
            transport=self._transport,
            base_url="https://api.speechify.ai",
        ) as client:
            while True:
                response = await client.get(
                    "/v1/voices",
                    params=params,
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                response.raise_for_status()
                payload = response.json()
                page = payload.get("voices")
                if not isinstance(page, list):
                    raise ValueError("Speechify voice response has no voices list")
                voices.extend(voice for voice in page if isinstance(voice, dict))
                cursor = payload.get("cursor") or payload.get("next_cursor")
                if (
                    not payload.get("has_more")
                    or not isinstance(cursor, str)
                    or not cursor
                    or cursor in seen_cursors
                ):
                    break
                seen_cursors.add(cursor)
                params["cursor"] = cursor
        return voices

    def _map_voices(
        self,
        provider_voices: list[dict[str, Any]],
        locale: Locale,
        model: str,
    ) -> list[VoiceCard]:
        base = LOCALES[locale].base
        default_provider_voice = next(
            (voice for voice in provider_voices if voice.get("id") == DEFAULT_VOICE_ID),
            None,
        )
        cards = [
            self._map_voice(voice, locale, model)
            for voice in provider_voices
            if voice.get("id") != DEFAULT_VOICE_ID
            and (
                (
                    locale == "multi"
                    and _locale_base(str(voice.get("locale", ""))) in MULTI_LANGUAGES
                )
                or (locale != "multi" and _locale_base(str(voice.get("locale", ""))) == base)
            )
        ]
        default = (
            self._map_voice(default_provider_voice, locale, model)
            if default_provider_voice is not None
            else self._default_voice()
        )
        return [default.model_copy(update={"is_default": True}), *cards]

    def _map_voice(self, voice: dict[str, Any], locale: Locale, model: str) -> VoiceCard:
        voice_locale = str(voice.get("locale") or locale)
        accent = next(
            (
                info.accent
                for code, info in LOCALES.items()
                if code.casefold() == voice_locale.casefold()
            ),
            next(
                (
                    info.accent
                    for code, info in LOCALES.items()
                    if info.base == _locale_base(voice_locale)
                ),
                voice_locale,
            ),
        )
        preview_url = voice.get("preview_audio")
        provider_models = voice.get("models")
        if isinstance(provider_models, list):
            selected_model = next(
                (
                    item
                    for item in provider_models
                    if isinstance(item, dict) and item.get("name") == model
                ),
                None,
            )
            if selected_model is not None and isinstance(selected_model.get("languages"), list):
                desired_base = LOCALES[locale].base
                language = next(
                    (
                        item
                        for item in selected_model["languages"]
                        if isinstance(item, dict)
                        and locale != "multi"
                        and str(item.get("locale", "")).casefold() == locale.casefold()
                    ),
                    None,
                )
                if language is None and locale != "multi":
                    language = next(
                        (
                            item
                            for item in selected_model["languages"]
                            if isinstance(item, dict)
                            and _locale_base(str(item.get("locale", ""))) == desired_base
                        ),
                        None,
                    )
                if language is not None and language.get("preview_audio"):
                    preview_url = language["preview_audio"]

        return VoiceCard(
            id=str(voice.get("id", "")),
            name=str(voice.get("display_name") or voice.get("id") or ""),
            gender=str(voice.get("gender") or ""),
            accent=accent,
            locale=voice_locale,
            preview_url=preview_url if isinstance(preview_url, str) else None,
            is_default=voice.get("id") == DEFAULT_VOICE_ID,
            pauses_mid_sentence=speechify_voice_pauses_mid_sentence(str(voice.get("id", ""))),
        )

    def _default_voice(self) -> VoiceCard:
        return VoiceCard(
            id=DEFAULT_VOICE_ID,
            name="Beatrice",
            gender="female",
            accent=LOCALES[DEFAULT_LOCALE].accent,
            locale=DEFAULT_LOCALE,
            preview_url=None,
            is_default=True,
            pauses_mid_sentence=False,
        )

    def _response(self, locale: Locale, voices: list[VoiceCard]) -> VoiceCatalogueResponse:
        available_locales = LOCALES.items()
        if get_settings().tts_provider != "speechify":
            available_locales = (
                (code, info) for code, info in available_locales if code != "multi"
            )
        return VoiceCatalogueResponse(
            locale=locale,
            locales=[
                VoiceLocaleOption(code=code, label=info.label, multilingual=code == "multi")
                for code, info in available_locales
            ],
            default_voice_id=DEFAULT_VOICE_ID,
            voices=voices,
        )


def _locale_base(locale: str) -> str:
    return locale.casefold().split("-", maxsplit=1)[0]


voice_catalogue = SpeechifyVoiceCatalogue()
