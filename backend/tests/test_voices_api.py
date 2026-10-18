async def test_voices_requires_authentication(api_client) -> None:
    response = await api_client.get("/voices")

    assert response.status_code == 401


async def test_voices_returns_catalogue_and_rejects_unknown_locale(
    tenant_pair,
    monkeypatch,
) -> None:
    from inbound_api.routers import voices as voices_router
    from inbound_api.settings import Settings

    monkeypatch.setattr(
        voices_router,
        "get_settings",
        lambda: Settings(_env_file=None),
    )
    client = tenant_pair["first"]["client"]
    response = await client.get("/voices?locale=de-DE")

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["provider"] == "speechify"
    assert body["locale"] == "de-DE"
    assert body["default_voice_id"] == "beatrice_32"
    assert body["voices"][0]["id"] == "beatrice_32"
    assert body["voices"][0]["is_default"] is True
    assert body["locales"][-1] == {
        "code": "multi",
        "label": "Multilingual",
        "multilingual": True,
    }

    invalid = await client.get("/voices?locale=xx-XX")
    assert invalid.status_code == 422
