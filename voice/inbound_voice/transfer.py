from __future__ import annotations

import asyncio
import json
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal, Protocol
from xml.etree import ElementTree as ET

import aiohttp
from inbound_core.config import TransferTarget
from loguru import logger


@dataclass(frozen=True)
class TransferStart:
    status: Literal["dialling", "simulated", "error"]
    error: str | None = None


class CallTransfer(Protocol):
    def prepare(self) -> None: ...

    async def start(self, target: TransferTarget, reason: str) -> TransferStart: ...

    async def outcome(self, timeout: float) -> str: ...


class SimulatedCallTransfer:
    def prepare(self) -> None:
        return None

    async def start(self, target: TransferTarget, reason: str) -> TransferStart:
        return TransferStart("simulated")

    async def outcome(self, timeout: float) -> str:
        return "simulated"


class TwilioCallControl(Protocol):
    async def update_call(self, call_sid: str, twiml: str) -> None: ...


class RestTwilioCallControl:
    def __init__(
        self,
        account_sid: str,
        auth_token: str,
        session_factory: Callable[..., Any] | None = None,
    ) -> None:
        self.account_sid = account_sid
        self.auth_token = auth_token
        self.session_factory = session_factory or aiohttp.ClientSession

    async def update_call(self, call_sid: str, twiml: str) -> None:
        url = f"https://api.twilio.com/2010-04-01/Accounts/{self.account_sid}/Calls/{call_sid}.json"
        async with self.session_factory(
            auth=aiohttp.BasicAuth(self.account_sid, self.auth_token),
            timeout=aiohttp.ClientTimeout(total=5),
        ) as session:
            async with session.post(url, data={"Twiml": twiml}) as response:
                response.raise_for_status()


class FakeTwilioCallControl:
    def __init__(self, log_path: str | Path | None = None) -> None:
        self.log_path = Path(log_path) if log_path is not None else None
        self.updates: list[tuple[str, str]] = []

    async def update_call(self, call_sid: str, twiml: str) -> None:
        self.updates.append((call_sid, twiml))
        if self.log_path is not None:
            self.log_path.parent.mkdir(parents=True, exist_ok=True)
            entry = {
                "at": datetime.now(UTC).isoformat(),
                "call_sid": call_sid,
                "twiml": twiml,
            }
            with self.log_path.open("a", encoding="utf-8") as log:
                log.write(json.dumps(entry, ensure_ascii=False) + "\n")


@dataclass
class TransferRegistryEntry:
    target: TransferTarget
    failed_message: str
    status: str | None = None
    future: asyncio.Future[str] | None = None


class TransferRegistry:
    def __init__(self) -> None:
        self._entries: dict[str, TransferRegistryEntry] = {}

    def register(
        self,
        call_sid: str,
        target: TransferTarget,
        failed_message: str,
    ) -> None:
        if call_sid in self._entries:
            return
        self._entries[call_sid] = TransferRegistryEntry(
            target=target,
            failed_message=failed_message,
        )

    def resolve(self, call_sid: str, status: str) -> None:
        entry = self._entries.get(call_sid)
        if entry is None or entry.status is not None:
            return
        entry.status = status
        if entry.future is not None and not entry.future.done():
            entry.future.set_result(status)

    async def wait(self, call_sid: str, timeout: float) -> str | None:
        entry = self._entries.get(call_sid)
        if entry is None:
            return None
        if entry.status is not None:
            return entry.status
        if entry.future is None:
            entry.future = asyncio.get_running_loop().create_future()
        try:
            return await asyncio.wait_for(asyncio.shield(entry.future), timeout)
        except TimeoutError:
            return None

    def entry(self, call_sid: str) -> TransferRegistryEntry | None:
        return self._entries.get(call_sid)

    def forget(self, call_sid: str) -> None:
        self._entries.pop(call_sid, None)


class TwilioCallTransfer:
    def __init__(
        self,
        call_control: TwilioCallControl,
        registry: TransferRegistry,
        call_sid: str,
        public_base_url: str,
        failed_message: str,
        on_prepare: Callable[[], None],
    ) -> None:
        self.call_control = call_control
        self.registry = registry
        self.call_sid = call_sid
        self.public_base_url = public_base_url.rstrip("/")
        self.failed_message = failed_message
        self.on_prepare = on_prepare

    def prepare(self) -> None:
        self.on_prepare()

    async def start(self, target: TransferTarget, reason: str) -> TransferStart:
        self.registry.register(self.call_sid, target, self.failed_message)
        twiml = build_transfer_twiml(
            target.phone,
            f"{self.public_base_url}/twilio/transfer-status",
            f"{self.public_base_url}/twilio/transfer-status?event=answered",
        )
        try:
            await self.call_control.update_call(self.call_sid, twiml)
        except Exception as error:
            logger.warning("Could not update Twilio call for transfer: {}", type(error).__name__)
            self.registry.forget(self.call_sid)
            return TransferStart("error", str(error))
        return TransferStart("dialling")

    async def outcome(self, timeout: float) -> str:
        try:
            return await self.registry.wait(self.call_sid, timeout) or "unknown"
        finally:
            self.registry.forget(self.call_sid)


def build_transfer_twiml(phone: str, action_url: str, answered_url: str) -> str:
    response = ET.Element("Response")
    dial = ET.SubElement(
        response,
        "Dial",
        {"timeout": "20", "action": action_url, "method": "POST"},
    )
    number = ET.SubElement(
        dial,
        "Number",
        {
            "statusCallbackEvent": "answered",
            "statusCallback": answered_url,
            "statusCallbackMethod": "POST",
        },
    )
    number.text = phone
    return ET.tostring(response, encoding="unicode")


def build_transfer_failed_twiml(message: str) -> str:
    response = ET.Element("Response")
    say = ET.SubElement(response, "Say")
    say.text = message
    ET.SubElement(response, "Hangup")
    return ET.tostring(response, encoding="unicode")


def build_transfer_hangup_twiml() -> str:
    response = ET.Element("Response")
    ET.SubElement(response, "Hangup")
    return ET.tostring(response, encoding="unicode")


def call_control_from_env(env: Mapping[str, str]) -> TwilioCallControl:
    account_sid = env.get("TWILIO_ACCOUNT_SID", "").strip()
    auth_token = env.get("TWILIO_AUTH_TOKEN", "").strip()
    mode = env.get("TWILIO_CALL_CONTROL", "").strip().lower()
    if not mode:
        mode = "rest" if account_sid and auth_token else "fake"
    if mode == "fake":
        log_path = env.get("TWILIO_FAKE_CALL_LOG", "").strip()
        return FakeTwilioCallControl(log_path or None)
    if mode == "rest":
        if not account_sid or not auth_token:
            raise ValueError("TWILIO_CALL_CONTROL=rest requires Twilio account SID and auth token.")
        return RestTwilioCallControl(account_sid, auth_token)
    raise ValueError("TWILIO_CALL_CONTROL must be 'rest' or 'fake'.")
