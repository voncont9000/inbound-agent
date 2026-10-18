from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from inbound_core.languages import LOCALES

from inbound_api.services.voice_catalogue import (
    CACHE_TTL_SECONDS,
    SpeechifyVoiceCatalogue,
)


def _voice(
    voice_id: str,
    locale: str,
    *,
    display_name: str | None = None,
    preview_audio: str | None = None,
    language_preview: str | None = None,
) -> dict:
    return {
        "id": voice_id,
        "display_name": display_name or voice_id,
        "gender": "female",
        "locale": locale,
        "preview_audio": preview_audio,
        "models": [
            {
                "name": "simba-3.0",
                "languages": [
                    {"locale": "de-DE", "preview_audio": language_preview},
                ],
            }
        ],
    }


@pytest.mark.asyncio
async def test_catalogue_maps_filters_and_follows_speechify_pages() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        query = parse_qs(urlparse(str(request.url)).query)
        assert request.headers["Authorization"] == "Bearer fake-speechify-key"
        assert query["type"] == ["shared"]
        assert query["model"] == ["simba-3.0"]
        assert query["limit"] == ["200"]
        if "cursor" not in query:
            assert query["locale"] == ["de"]
            return httpx.Response(
                200,
                json={
                    "voices": [
                        _voice(
                            "german-voice",
                            "de-DE",
                            display_name="German Voice",
                            preview_audio="https://audio.test/german-default.mp3",
                            language_preview="https://audio.test/german.mp3",
                        ),
                        _voice("english-voice", "en-GB"),
                    ],
                    "has_more": True,
                    "cursor": "next-page",
                },
            )
        assert query["cursor"] == ["next-page"]
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice(
                        "beatrice_32",
                        "en-GB",
                        display_name="Beatrice",
                        preview_audio="https://audio.test/beatrice-default.mp3",
                        language_preview="https://audio.test/beatrice-german.mp3",
                    )
                ],
                "has_more": False,
            },
        )

    catalogue = SpeechifyVoiceCatalogue(transport=httpx.MockTransport(handler))
    response = await catalogue.get("de-DE", "fake-speechify-key")

    assert len(requests) == 2
    assert response.provider == "speechify"
    assert response.locale == "de-DE"
    assert response.default_voice_id == "beatrice_32"
    assert response.voices[0].id == "beatrice_32"
    assert response.voices[0].is_default is True
    assert response.voices[0].preview_url == "https://audio.test/beatrice-german.mp3"
    assert response.voices[1].id == "german-voice"
    assert response.voices[1].name == "German Voice"
    assert response.voices[1].accent == "German"
    assert response.voices[1].preview_url == "https://audio.test/german.mp3"
    assert len(response.locales) == len(LOCALES)
    assert response.locales[-1].code == "multi"
    assert response.locales[-1].multilingual is True


@pytest.mark.asyncio
async def test_multilingual_catalogue_omits_locale_filter_and_limits_supported_voices() -> None:
    requests: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(
            200,
            json={
                "voices": [
                    _voice("english", "en-US"),
                    _voice("portuguese", "pt-BR"),
                    _voice("japanese", "ja-JP"),
                ],
                "has_more": False,
            },
        )

    catalogue = SpeechifyVoiceCatalogue(transport=httpx.MockTransport(handler))
    response = await catalogue.get("multi", "fake-speechify-key")

    query = parse_qs(urlparse(str(requests[0].url)).query)
    assert "locale" not in query
    assert [voice.id for voice in response.voices] == [
        "beatrice_32",
        "english",
        "portuguese",
    ]


@pytest.mark.asyncio
async def test_catalogue_cache_expires_after_24_hours() -> None:
    now = [100.0]
    requests = 0

    def handler(_: httpx.Request) -> httpx.Response:
        nonlocal requests
        requests += 1
        return httpx.Response(
            200,
            json={"voices": [_voice("french", "fr-FR")], "has_more": False},
        )

    catalogue = SpeechifyVoiceCatalogue(
        clock=lambda: now[0],
        transport=httpx.MockTransport(handler),
    )

    first = await catalogue.get("fr-FR", "fake-speechify-key")
    second = await catalogue.get("fr-FR", "fake-speechify-key")
    assert first.voices == second.voices
    assert requests == 1

    await catalogue.get("en-GB", "fake-speechify-key")
    assert requests == 2

    now[0] += CACHE_TTL_SECONDS + 1
    await catalogue.get("fr-FR", "fake-speechify-key")
    assert requests == 3


@pytest.mark.asyncio
async def test_catalogue_returns_static_default_when_key_missing_or_provider_fails() -> None:
    def fail_request(_: httpx.Request) -> httpx.Response:
        return httpx.Response(503)

    no_key_catalogue = SpeechifyVoiceCatalogue(
        transport=httpx.MockTransport(
            lambda _: (_ for _ in ()).throw(AssertionError("provider must not be called"))
        )
    )
    provider_error_catalogue = SpeechifyVoiceCatalogue(transport=httpx.MockTransport(fail_request))

    no_key = await no_key_catalogue.get("de-DE", None)
    failed = await provider_error_catalogue.get("de-DE", "fake-speechify-key")

    for response in (no_key, failed):
        assert response.default_voice_id == "beatrice_32"
        assert len(response.voices) == 1
        assert response.voices[0].id == "beatrice_32"
        assert response.voices[0].name == "Beatrice"
        assert response.voices[0].locale == "en-GB"
        assert response.voices[0].accent == "British"
        assert response.voices[0].preview_url is None
    assert "fake-speechify-key" not in failed.model_dump_json()
