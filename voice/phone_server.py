from __future__ import annotations

import argparse
import asyncio
import os
import time
from collections.abc import Mapping
from contextlib import asynccontextmanager
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from inbound_core.config import AssistantConfig
from loguru import logger
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.datastructures import FormData
from starlette.websockets import WebSocketState

from bot import resolve_caller_id, run_bot
from inbound_voice.api_client import UnknownChannelError
from inbound_voice.call_setup import prepare_call
from inbound_voice.replay import RedisReplayGuard, ReplayGuard, replay_guard_from_env
from inbound_voice.transfer import (
    TransferRegistry,
    TwilioCallControl,
    TwilioCallTransfer,
    build_transfer_failed_twiml,
    build_transfer_hangup_twiml,
    call_control_from_env,
)
from inbound_voice.twilio import (
    STREAM_TOKEN_MAX_AGE_SECONDS,
    build_stream_twiml,
    parse_start_message,
    stream_token,
    validate_signature,
    verify_stream_token,
)

VOICE_ROOT = Path(__file__).resolve().parent


def create_app(
    env: Mapping[str, str] = os.environ,
    replay_guard: ReplayGuard | None = None,
    call_control: TwilioCallControl | None = None,
) -> FastAPI:
    app_env = env.get("APP_ENV", "development")
    twilio_mode = env.get("TWILIO_MODE", "twilio")
    if twilio_mode not in {"twilio", "off"}:
        raise ValueError("TWILIO_MODE must be 'twilio' or 'off'")
    if (
        app_env not in {"development", "test"}
        and twilio_mode == "twilio"
        and not env.get("TWILIO_AUTH_TOKEN", "").strip()
    ):
        raise ValueError("TWILIO_AUTH_TOKEN is required in production when TWILIO_MODE=twilio")
    guard = replay_guard if replay_guard is not None else replay_guard_from_env(env)
    owns_guard = replay_guard is None
    selected_call_control = call_control if call_control is not None else call_control_from_env(env)
    transfer_registry = TransferRegistry()

    @asynccontextmanager
    async def lifespan(_: FastAPI):
        try:
            yield
        finally:
            if owns_guard and isinstance(guard, RedisReplayGuard):
                await guard.client.aclose()

    app = FastAPI(lifespan=lifespan)
    app.state.replay_guard = guard
    app.state.call_control = selected_call_control
    app.state.transfer_registry = transfer_registry

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/twilio/voice")
    async def voice_webhook(request: Request) -> Response:
        if twilio_mode == "off":
            raise HTTPException(status_code=503, detail="Twilio is disabled")
        form = await request.form()
        params = _form_params(form)
        _verify_twilio_signature(request, params, env)
        public_base_url = env.get("PUBLIC_BASE_URL", "").strip()
        auth_token = env.get("TWILIO_AUTH_TOKEN", "").strip()
        ws_url = (
            _websocket_url(public_base_url)
            if public_base_url
            else _websocket_url(_external_base_url(request))
        )
        stream_parameters = {"To": params.get("To", ""), "From": params.get("From", "")}
        if auth_token:
            issued_at = int(time.time())
            stream_parameters["StreamIssuedAt"] = str(issued_at)
            stream_parameters["StreamToken"] = stream_token(
                auth_token,
                params.get("CallSid", ""),
                stream_parameters["To"],
                stream_parameters["From"],
                issued_at,
            )
        twiml = build_stream_twiml(
            f"{ws_url}/twilio/ws",
            stream_parameters,
        )
        return Response(content=twiml, media_type="application/xml")

    @app.post("/twilio/transfer-status")
    async def transfer_status_webhook(request: Request) -> Response:
        form = await request.form()
        params = _form_params(form)
        _verify_twilio_signature(request, params, env)
        if request.query_params.get("event") == "answered":
            transfer_registry.resolve(params.get("ParentCallSid", ""), "answered")
            return Response(content="<Response/>", media_type="application/xml")

        call_sid = params.get("CallSid", "")
        status = params.get("DialCallStatus", "") or "unknown"
        transfer_registry.resolve(call_sid, status)
        if status in {"completed", "answered"}:
            twiml = build_transfer_hangup_twiml()
        else:
            entry = transfer_registry.entry(call_sid)
            message = (
                entry.failed_message
                if entry is not None
                else AssistantConfig.model_fields["transfer_failed_message"].default
            )
            twiml = build_transfer_failed_twiml(message)
        return Response(content=twiml, media_type="application/xml")

    @app.websocket("/twilio/ws")
    async def twilio_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        if twilio_mode == "off":
            await websocket.close(code=1013)
            return
        try:
            start_payload = await _read_start_message(websocket)
            if start_payload is None:
                return
            start = parse_start_message({"event": "start", "start": start_payload})
            auth_token = env.get("TWILIO_AUTH_TOKEN", "").strip()
            if auth_token and not verify_stream_token(
                auth_token,
                start.call_sid,
                start.custom_parameters.get("To", ""),
                start.custom_parameters.get("From", ""),
                start.custom_parameters.get("StreamIssuedAt", ""),
                start.custom_parameters.get("StreamToken", ""),
            ):
                logger.warning("Rejected Twilio media stream with invalid StreamToken.")
                await websocket.close(code=1008)
                return
            replay_window = STREAM_TOKEN_MAX_AGE_SECONDS + 30
            if not await guard.claim(f"twilio:{start.call_sid}", replay_window):
                logger.warning("Rejected duplicate Twilio media stream for an accepted call.")
                await websocket.close(code=1008)
                return
            caller_id = resolve_caller_id({"start": start_payload}, env, telephony=True)
            prepared = await prepare_call(
                env,
                channel="phone",
                to_number=start.to_number,
                from_number=start.from_number,
                external_call_id=start.call_sid,
            )
            account_sid = env.get("TWILIO_ACCOUNT_SID", "").strip()
            serializer = TransferAwareTwilioFrameSerializer(
                stream_sid=start.stream_sid,
                call_sid=start.call_sid,
                account_sid=account_sid or None,
                auth_token=auth_token or None,
                params=TwilioFrameSerializer.InputParams(
                    auto_hang_up=bool(account_sid and auth_token),
                    sample_rate=8000,
                ),
            )
            call_transfer = TwilioCallTransfer(
                selected_call_control,
                transfer_registry,
                start.call_sid,
                _websocket_callback_base_url(websocket, env),
                prepared.assistant.transfer_failed_message,
                serializer.disable_auto_hang_up,
            )
            transport = FastAPIWebsocketTransport(
                websocket,
                FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    audio_in_sample_rate=8000,
                    audio_out_sample_rate=8000,
                    add_wav_header=False,
                    serializer=serializer,
                ),
            )
            await run_bot(
                transport,
                telephony=True,
                caller_id=caller_id,
                prepared=prepared,
                call_id=start.call_sid,
                call_transfer=call_transfer,
            )
        except WebSocketDisconnect:
            return
        except UnknownChannelError:
            logger.warning("No voice route for destination {}; ending call.", start.to_number)
            await websocket.close(code=1000)
            return
        except Exception as error:
            logger.exception("Twilio media stream failed: {}", error)
            try:
                await websocket.close(code=1011)
            except RuntimeError:
                pass
        finally:
            if websocket.client_state != WebSocketState.DISCONNECTED:
                try:
                    await websocket.close()
                except RuntimeError:
                    pass

    return app


class TransferAwareTwilioFrameSerializer(TwilioFrameSerializer):
    def disable_auto_hang_up(self) -> None:
        self._hangup_attempted = True


def _verify_twilio_signature(
    request: Request,
    params: Mapping[str, str],
    env: Mapping[str, str],
) -> None:
    auth_token = env.get("TWILIO_AUTH_TOKEN", "").strip()
    if not auth_token:
        logger.warning("TWILIO_AUTH_TOKEN is unset; skipping webhook signature validation.")
        return
    public_base_url = env.get("PUBLIC_BASE_URL", "").strip()
    webhook_url = (
        f"{public_base_url.rstrip('/')}{request.url.path}"
        + (f"?{request.url.query}" if request.url.query else "")
        if public_base_url
        else _external_request_url(request)
    )
    signature = request.headers.get("X-Twilio-Signature", "")
    if not signature or not validate_signature(auth_token, webhook_url, params, signature):
        raise HTTPException(status_code=403, detail="Invalid Twilio signature")


async def _read_start_message(websocket: WebSocket) -> dict | None:
    deadline = asyncio.get_running_loop().time() + 10
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            await websocket.close(code=1008)
            return None
        try:
            message = await asyncio.wait_for(websocket.receive_json(), timeout=remaining)
        except TimeoutError:
            await websocket.close(code=1008)
            return None
        event = message.get("event")
        if event == "start":
            return message.get("start", {})
        if event in {"stop", "closed"}:
            return None


def _form_params(form: FormData) -> dict[str, str]:
    return {str(key): str(value) for key, value in form.multi_items()}


def _external_base_url(request: Request) -> str:
    forwarded_proto = request.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    forwarded_host = request.headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or request.url.scheme
    host = forwarded_host or request.headers.get("host") or request.url.netloc
    return f"{scheme}://{host}"


def _external_request_url(request: Request) -> str:
    base = _external_base_url(request)
    return f"{base}{request.url.path}" + (f"?{request.url.query}" if request.url.query else "")


def _websocket_callback_base_url(websocket: WebSocket, env: Mapping[str, str]) -> str:
    public_base_url = env.get("PUBLIC_BASE_URL", "").strip()
    if public_base_url:
        return public_base_url.rstrip("/")
    headers = websocket.headers
    forwarded_proto = headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip()
    forwarded_host = headers.get("X-Forwarded-Host", "").split(",", 1)[0].strip()
    scheme = forwarded_proto or websocket.url.scheme
    scheme = {"ws": "http", "wss": "https"}.get(scheme, scheme)
    host = forwarded_host or headers.get("host") or websocket.url.netloc
    return f"{scheme}://{host}"


def _websocket_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :].rstrip("/")
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :].rstrip("/")
    return base_url.rstrip("/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the inbound Twilio voice server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7861)
    return parser


def main() -> None:
    load_dotenv(VOICE_ROOT / ".env")
    args = build_parser().parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
