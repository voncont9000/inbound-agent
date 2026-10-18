from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from pathlib import Path
from uuid import UUID

import httpx
import pytest
from fastapi.testclient import TestClient
from inbound_core.call_report import CallReport, CallStart, RouteResponse

from eval.fake_api import build_parser as build_fake_api_parser
from eval.fake_api import create_fake_api
from inbound_voice.api_client import ApiSettings, VoiceApiClient, api_settings_from_env
from inbound_voice.call_report import build_call_report
from inbound_voice.config import load_assistant_config

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "eval-call"
ASSISTANT_PATH = Path(__file__).parents[1] / "config" / "assistants" / "demo_firm.yaml"


def _route_response() -> RouteResponse:
    return RouteResponse(
        tenant_id=UUID("10000000-0000-0000-0000-000000000001"),
        assistant_id=UUID("20000000-0000-0000-0000-000000000002"),
        config=load_assistant_config(ASSISTANT_PATH),
    )


def _call_start() -> CallStart:
    return CallStart(
        to_number="+441234567890",
        from_number="+447700900001",
        external_call_id="CA-test-call",
        channel="phone",
        started_at=datetime(2026, 10, 18, tzinfo=UTC),
    )


def _report() -> CallReport:
    return build_call_report(FIXTURE_DIR, "CA-test-call")


def _client(
    app,
    spool_dir: Path,
    *,
    sleep,
) -> VoiceApiClient:
    return VoiceApiClient(
        ApiSettings("http://fake", "test-service-token"),
        spool_dir=spool_dir,
        transport=httpx.ASGITransport(app=app),
        sleep=sleep,
    )


@pytest.mark.asyncio
async def test_route_start_and_report_happy_path(tmp_path: Path) -> None:
    app = create_fake_api(_route_response(), "test-service-token")
    client = _client(app, tmp_path, sleep=_async_noop)

    route = await client.route("+441234567890", "+447700900001")
    call_start = _call_start()
    conversation_id = await client.start_call(call_start)
    posted = await client.post_report(conversation_id, call_start, _report())

    assert route.config.business_name == load_assistant_config(ASSISTANT_PATH).business_name
    assert posted is True
    assert len(app.state.fake["calls"]) == 1
    assert app.state.fake["reports"][0]["conversation_id"] == str(conversation_id)
    assert app.state.fake["reports"][0]["external_call_id"] == "CA-test-call"


@pytest.mark.asyncio
async def test_fake_api_rejects_missing_and_invalid_service_tokens() -> None:
    app = create_fake_api(_route_response(), "test-service-token")
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(base_url="http://fake", transport=transport) as client:
        missing = await client.get("/internal/voice/route", params={"to": "+441234567890"})
        invalid = await client.get(
            "/internal/voice/route",
            params={"to": "+441234567890"},
            headers={"X-Service-Token": "wrong-token"},
        )
        missing_state_token = await client.get("/_fake/state")
        authorized_state = await client.get(
            "/_fake/state",
            headers={"X-Service-Token": "test-service-token"},
        )

    assert missing.status_code == 401
    assert invalid.status_code == 401
    assert missing_state_token.status_code == 401
    assert authorized_state.status_code == 200


def test_fake_api_host_defaults_to_loopback() -> None:
    assert build_fake_api_parser().parse_args([]).host == "127.0.0.1"


@pytest.mark.asyncio
async def test_report_retries_retryable_statuses_with_expected_delays(tmp_path: Path) -> None:
    app = create_fake_api(_route_response(), "test-service-token", fail_reports=2)
    delays: list[float] = []
    client = _client(app, tmp_path, sleep=_record_sleep(delays))
    call_start = _call_start()

    conversation_id = await client.start_call(call_start)
    assert await client.post_report(conversation_id, call_start, _report()) is True

    assert delays == [0.5, 1.0]
    assert len(app.state.fake["reports"]) == 1
    assert not list(tmp_path.glob("*.json"))


@pytest.mark.asyncio
async def test_final_report_failure_spools_json_payload(tmp_path: Path) -> None:
    app = create_fake_api(_route_response(), "test-service-token", fail_reports=10)
    delays: list[float] = []
    client = _client(app, tmp_path, sleep=_record_sleep(delays))
    call_start = _call_start()
    conversation_id = UUID("30000000-0000-0000-0000-000000000003")

    assert await client.post_report(conversation_id, call_start, _report()) is False

    spool_file = tmp_path / "CA-test-call.json"
    payload = json.loads(spool_file.read_text(encoding="utf-8"))
    assert payload["conversation_id"] == str(conversation_id)
    assert payload["call_start"]["external_call_id"] == "CA-test-call"
    assert payload["report"]["external_call_id"] == "CA-test-call"
    assert payload["last_error"] == "API returned HTTP 503"
    assert payload["spooled_at"]
    assert delays == [0.5, 1.0, 2.0, 4.0]
    assert "service_token" not in spool_file.read_text(encoding="utf-8")


@pytest.mark.asyncio
async def test_non_retryable_report_failure_spools_without_sleep(tmp_path: Path) -> None:
    def handle(request: httpx.Request) -> httpx.Response:
        return httpx.Response(400, request=request)

    client = VoiceApiClient(
        ApiSettings("http://fake", "test-service-token"),
        spool_dir=tmp_path,
        transport=httpx.MockTransport(handle),
        sleep=_unexpected_sleep,
    )

    assert (
        await client.post_report(
            UUID("30000000-0000-0000-0000-000000000003"),
            _call_start(),
            _report(),
        )
        is False
    )
    assert (tmp_path / "CA-test-call.json").exists()


def test_spool_hashes_unsafe_external_call_ids_into_spool_directory(tmp_path: Path) -> None:
    client = VoiceApiClient(
        ApiSettings("http://fake", "test-service-token"),
        spool_dir=tmp_path,
        sleep=_async_noop,
    )
    external_call_ids = ("../../evil", "a/b")

    for external_call_id in external_call_ids:
        call_start = _call_start().model_copy(update={"external_call_id": external_call_id})
        report = _report().model_copy(update={"external_call_id": external_call_id})
        client._spool(call_start, report, None, "API unavailable")
        digest = hashlib.sha256(external_call_id.encode("utf-8")).hexdigest()[:16]
        spool_file = tmp_path / f"call-{digest}.json"

        assert spool_file.exists()
        assert spool_file.parent.resolve() == tmp_path.resolve()
        assert (
            json.loads(spool_file.read_text(encoding="utf-8"))["call_start"]["external_call_id"]
            == external_call_id
        )

    assert len(list(tmp_path.glob("*.json"))) == 2


@pytest.mark.asyncio
async def test_failed_start_is_spooled_and_resend_starts_then_reports(tmp_path: Path) -> None:
    app = create_fake_api(_route_response(), "test-service-token")
    failures = _FailStartTransport(httpx.ASGITransport(app=app), remaining=5)
    failed_client = VoiceApiClient(
        ApiSettings("http://fake", "test-service-token"),
        spool_dir=tmp_path,
        transport=failures,
        sleep=_async_noop,
    )
    call_start = _call_start()

    assert await failed_client.post_report(None, call_start, _report()) is False
    assert json.loads((tmp_path / "CA-test-call.json").read_text())["conversation_id"] is None

    resend_client = _client(app, tmp_path, sleep=_async_noop)
    assert await resend_client.resend_spooled(tmp_path) == (1, 0)
    assert not (tmp_path / "CA-test-call.json").exists()
    assert len(app.state.fake["calls"]) == 1
    assert len(app.state.fake["reports"]) == 1


def test_api_settings_require_both_non_empty_environment_values() -> None:
    assert api_settings_from_env({}) is None
    assert api_settings_from_env({"API_BASE_URL": "https://api.example"}) is None
    assert api_settings_from_env({"VOICE_SERVICE_TOKEN": "token"}) is None
    settings = api_settings_from_env(
        {
            "API_BASE_URL": "https://api.example/",
            "VOICE_SERVICE_TOKEN": "token",
            "ROUTE_TIMEOUT_SECONDS": "3.5",
        }
    )
    assert settings is not None
    assert settings.base_url == "https://api.example"
    assert settings.route_timeout == 3.5


def test_fake_api_rejects_bad_token_and_mismatched_destination() -> None:
    app = create_fake_api(
        _route_response(),
        "test-service-token",
        to_number="+441234567890",
    )
    with TestClient(app) as client:
        bad_token = client.get(
            "/internal/voice/route",
            params={"to": "+441234567890"},
            headers={"X-Service-Token": "wrong"},
        )
        wrong_destination = client.get(
            "/internal/voice/route",
            params={"to": "+441234567891"},
            headers={"X-Service-Token": "test-service-token"},
        )
    assert bad_token.status_code == 401
    assert wrong_destination.status_code == 404


async def _async_noop(_: float) -> None:
    return None


def _record_sleep(delays: list[float]):
    async def sleep(delay: float) -> None:
        delays.append(delay)

    return sleep


async def _unexpected_sleep(_: float) -> None:
    raise AssertionError("Non-retryable errors must not sleep.")


class _FailStartTransport(httpx.AsyncBaseTransport):
    def __init__(self, delegate: httpx.AsyncBaseTransport, remaining: int) -> None:
        self.delegate = delegate
        self.remaining = remaining

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        if request.url.path == "/internal/voice/calls" and self.remaining:
            self.remaining -= 1
            return httpx.Response(503, request=request)
        return await self.delegate.handle_async_request(request)

    async def aclose(self) -> None:
        await self.delegate.aclose()
