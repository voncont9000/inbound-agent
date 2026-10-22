from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import math
import os
import re
import tempfile
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

import httpx
from inbound_core.booking import (
    AvailabilityRequest,
    AvailabilityResponse,
    BookRequest,
    BookResponse,
)
from inbound_core.call_report import CallReport, CallStart, RouteResponse
from loguru import logger

RETRY_DELAYS = (0.5, 1.0, 2.0, 4.0)
RETRYABLE_STATUSES = {408, 429}
DEFAULT_SPOOL_DIR = Path(__file__).resolve().parents[1] / "spool"


@dataclass(frozen=True)
class ApiSettings:
    base_url: str
    service_token: str
    route_timeout: float = 2.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "base_url", self.base_url.rstrip("/"))


def api_settings_from_env(env: Mapping[str, str]) -> ApiSettings | None:
    base_url = env.get("API_BASE_URL", "").strip()
    service_token = env.get("VOICE_SERVICE_TOKEN", "").strip()
    if not base_url or not service_token:
        return None
    try:
        route_timeout = float(env.get("ROUTE_TIMEOUT_SECONDS", "2.0"))
    except ValueError as error:
        raise ValueError("ROUTE_TIMEOUT_SECONDS must be a positive number.") from error
    if not math.isfinite(route_timeout) or route_timeout <= 0:
        raise ValueError("ROUTE_TIMEOUT_SECONDS must be a positive number.")
    return ApiSettings(base_url, service_token, route_timeout)


class UnknownChannelError(RuntimeError):
    pass


class VoiceApiClient:
    def __init__(
        self,
        settings: ApiSettings,
        *,
        spool_dir: str | Path,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep=asyncio.sleep,
    ) -> None:
        self.settings = settings
        self.spool_dir = Path(spool_dir)
        self.transport = transport
        self.sleep = sleep

    async def route(self, to_number: str, from_number: str | None) -> RouteResponse:
        async with self._client(timeout=self.settings.route_timeout) as client:
            response = await client.get(
                "/internal/voice/route",
                params={"to": to_number, "from": from_number},
            )
            if response.status_code == 404:
                raise UnknownChannelError("Unknown phone number")
            response.raise_for_status()
            return RouteResponse.model_validate(response.json())

    async def route_web_call(self, token: str) -> RouteResponse:
        async with self._client(timeout=self.settings.route_timeout) as client:
            response = await client.get(
                "/internal/voice/web-route",
                params={"token": token},
            )
            response.raise_for_status()
            return RouteResponse.model_validate(response.json())

    async def availability(
        self, req: AvailabilityRequest, timeout: float = 4.0
    ) -> AvailabilityResponse:
        async with self._client(timeout=timeout) as client:
            response = await client.post(
                "/internal/voice/availability", json=req.model_dump(mode="json")
            )
            response.raise_for_status()
            return AvailabilityResponse.model_validate(response.json())

    async def book(self, req: BookRequest, timeout: float = 8.0) -> BookResponse:
        async with self._client(timeout=timeout) as client:
            response = await client.post("/internal/voice/book", json=req.model_dump(mode="json"))
            response.raise_for_status()
            return BookResponse.model_validate(response.json())

    async def start_call(self, call_start: CallStart) -> UUID:
        last_error: Exception | None = None
        async with self._client() as client:
            for attempt in range(5):
                try:
                    response = await client.post(
                        "/internal/voice/calls",
                        json=call_start.model_dump(mode="json"),
                    )
                    if self._should_retry_status(response.status_code) and attempt < 4:
                        await self.sleep(RETRY_DELAYS[attempt])
                        continue
                    response.raise_for_status()
                    return UUID(response.json()["conversation_id"])
                except (httpx.TransportError, httpx.TimeoutException) as error:
                    last_error = error
                    if attempt == 4:
                        break
                    await self.sleep(RETRY_DELAYS[attempt])
                except httpx.HTTPStatusError as error:
                    if not self._should_retry_status(error.response.status_code) or attempt == 4:
                        raise
                    last_error = error
                    await self.sleep(RETRY_DELAYS[attempt])
        if last_error is not None:
            raise RuntimeError(f"Could not start voice call: {last_error}") from last_error
        raise RuntimeError("Could not start voice call.")

    async def post_report(
        self,
        conversation_id: UUID | None,
        call_start: CallStart,
        report: CallReport,
    ) -> bool:
        last_error = "Unknown API failure"
        async with self._client() as client:
            for attempt in range(5):
                try:
                    if conversation_id is None:
                        conversation_id = await self._start_call_once(client, call_start)
                    response = await client.post(
                        f"/internal/voice/calls/{conversation_id}/report",
                        json=report.model_dump(mode="json"),
                    )
                    if self._should_retry_status(response.status_code):
                        last_error = f"API returned HTTP {response.status_code}"
                        if attempt < 4:
                            await self.sleep(RETRY_DELAYS[attempt])
                            continue
                        break
                    response.raise_for_status()
                    return True
                except (httpx.TransportError, httpx.TimeoutException) as error:
                    last_error = f"{type(error).__name__}: {error}"
                    if attempt == 4:
                        break
                    await self.sleep(RETRY_DELAYS[attempt])
                except httpx.HTTPStatusError as error:
                    status = error.response.status_code
                    last_error = f"API returned HTTP {status}"
                    if self._should_retry_status(status) and attempt < 4:
                        await self.sleep(RETRY_DELAYS[attempt])
                        continue
                    break
                except Exception as error:
                    last_error = f"{type(error).__name__}: {error}"
                    break
        try:
            self._spool(call_start, report, conversation_id, last_error)
        except OSError as error:
            logger.warning(
                "Could not spool voice report for call {}: {}",
                call_start.external_call_id,
                error,
            )
        return False

    async def upload_recording(
        self,
        conversation_id: UUID,
        external_call_id: str,
        path: str | Path,
        *,
        duration_seconds: float,
    ) -> bool:
        body = Path(path).read_bytes()
        last_error = "Unknown API failure"
        async with self._client(timeout=60.0) as client:
            for attempt in range(5):
                try:
                    response = await client.post(
                        f"/internal/voice/calls/{conversation_id}/recording",
                        params={"duration_seconds": duration_seconds},
                        headers={
                            "X-External-Call-ID": external_call_id,
                            "Content-Type": "audio/wav",
                        },
                        content=body,
                    )
                    if self._should_retry_status(response.status_code):
                        last_error = f"API returned HTTP {response.status_code}"
                        if attempt < 4:
                            await self.sleep(RETRY_DELAYS[attempt])
                            continue
                        break
                    response.raise_for_status()
                    return True
                except (httpx.TransportError, httpx.TimeoutException) as error:
                    last_error = f"{type(error).__name__}: {error}"
                    if attempt == 4:
                        break
                    await self.sleep(RETRY_DELAYS[attempt])
                except httpx.HTTPStatusError as error:
                    status = error.response.status_code
                    last_error = f"API returned HTTP {status}"
                    if self._should_retry_status(status) and attempt < 4:
                        await self.sleep(RETRY_DELAYS[attempt])
                        continue
                    break
        logger.warning(
            "Could not upload voice recording for call {}: {}",
            external_call_id,
            last_error,
        )
        return False

    async def resend_spooled(self, spool_dir: str | Path) -> tuple[int, int]:
        directory = Path(spool_dir)
        sent = 0
        files = sorted(directory.glob("*.json")) if directory.exists() else []
        for path in files:
            try:
                payload = json.loads(path.read_text(encoding="utf-8"))
                call_start = CallStart.model_validate(payload["call_start"])
                report = CallReport.model_validate(payload["report"])
                conversation_id = (
                    UUID(payload["conversation_id"]) if payload.get("conversation_id") else None
                )
                if await self.post_report(conversation_id, call_start, report):
                    path.unlink(missing_ok=True)
                    sent += 1
                else:
                    latest = self._read_spooled(path)
                    self._atomic_write(path, latest)
            except Exception as error:
                try:
                    payload = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError):
                    continue
                payload["last_error"] = f"{type(error).__name__}: {error}"
                self._atomic_write(path, payload)
        remaining = sum(1 for _ in directory.glob("*.json")) if directory.exists() else 0
        return sent, remaining

    def _client(self, timeout: float = 10.0) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.settings.base_url,
            headers={"X-Service-Token": self.settings.service_token},
            timeout=timeout,
            transport=self.transport,
        )

    async def _start_call_once(self, client: httpx.AsyncClient, call_start: CallStart) -> UUID:
        response = await client.post(
            "/internal/voice/calls",
            json=call_start.model_dump(mode="json"),
        )
        response.raise_for_status()
        return UUID(response.json()["conversation_id"])

    @staticmethod
    def _should_retry_status(status: int) -> bool:
        return status in RETRYABLE_STATUSES or status >= 500

    def _spool(
        self,
        call_start: CallStart,
        report: CallReport,
        conversation_id: UUID | None,
        last_error: str,
    ) -> None:
        external_call_id = call_start.external_call_id
        if re.fullmatch(r"[A-Za-z0-9_-]{1,128}", external_call_id):
            filename = external_call_id
        else:
            digest = hashlib.sha256(external_call_id.encode("utf-8")).hexdigest()[:16]
            filename = f"call.{digest}"
        path = self.spool_dir / f"{filename}.json"
        payload = {
            "conversation_id": str(conversation_id) if conversation_id else None,
            "call_start": call_start.model_dump(mode="json"),
            "report": report.model_dump(mode="json"),
            "last_error": last_error,
            "spooled_at": datetime.now(UTC).isoformat(),
        }
        self._atomic_write(path, payload)

    @staticmethod
    def _atomic_write(path: Path, payload: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.")
        try:
            with os.fdopen(descriptor, "w", encoding="utf-8") as output:
                json.dump(payload, output, indent=2, ensure_ascii=False)
                output.write("\n")
            os.replace(temporary, path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    @staticmethod
    def _read_spooled(path: Path) -> dict[str, Any]:
        return json.loads(path.read_text(encoding="utf-8"))


async def _resend_command(spool_dir: Path) -> int:
    settings = api_settings_from_env(os.environ)
    if settings is None:
        print("API_BASE_URL and VOICE_SERVICE_TOKEN must be set.")
        return 1
    sent, remaining = await VoiceApiClient(settings, spool_dir=spool_dir).resend_spooled(spool_dir)
    print(f"Sent {sent}; remaining {remaining}.")
    return 1 if remaining else 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Voice API reporting utilities.")
    commands = parser.add_subparsers(dest="command", required=True)
    resend = commands.add_parser("resend", help="resend spooled call reports")
    resend.add_argument("--spool-dir", type=Path, default=DEFAULT_SPOOL_DIR)
    args = parser.parse_args()
    raise SystemExit(asyncio.run(_resend_command(args.spool_dir)))


if __name__ == "__main__":
    main()
