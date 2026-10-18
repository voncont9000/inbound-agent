from __future__ import annotations

import argparse
import asyncio
import os
from collections.abc import Mapping
from pathlib import Path

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Request, WebSocket, WebSocketDisconnect
from fastapi.responses import Response
from loguru import logger
from pipecat.serializers.twilio import TwilioFrameSerializer
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.datastructures import FormData

from bot import resolve_caller_id, run_bot
from inbound_voice.call_setup import prepare_call
from inbound_voice.twilio import (
    build_stream_twiml,
    parse_start_message,
    validate_signature,
)

VOICE_ROOT = Path(__file__).resolve().parent


def create_app(env: Mapping[str, str] = os.environ) -> FastAPI:
    app = FastAPI()

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.post("/twilio/voice")
    async def voice_webhook(request: Request) -> Response:
        form = await request.form()
        params = _form_params(form)
        auth_token = env.get("TWILIO_AUTH_TOKEN", "").strip()
        public_base_url = env.get("PUBLIC_BASE_URL", "").strip()
        webhook_url = (
            f"{public_base_url.rstrip('/')}/twilio/voice"
            if public_base_url
            else _external_request_url(request)
        )
        if auth_token:
            signature = request.headers.get("X-Twilio-Signature", "")
            if not signature or not validate_signature(auth_token, webhook_url, params, signature):
                raise HTTPException(status_code=403, detail="Invalid Twilio signature")
        else:
            logger.warning("TWILIO_AUTH_TOKEN is unset; skipping webhook signature validation.")
        ws_url = (
            _websocket_url(public_base_url)
            if public_base_url
            else _websocket_url(_external_base_url(request))
        )
        twiml = build_stream_twiml(
            f"{ws_url}/twilio/ws",
            {"To": params.get("To", ""), "From": params.get("From", "")},
        )
        return Response(content=twiml, media_type="application/xml")

    @app.websocket("/twilio/ws")
    async def twilio_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        try:
            start_payload = await _read_start_message(websocket)
            if start_payload is None:
                return
            start = parse_start_message({"event": "start", "start": start_payload})
            caller_id = resolve_caller_id({"start": start_payload}, env, telephony=True)
            prepared = await prepare_call(
                env,
                channel="phone",
                to_number=start.to_number,
                from_number=start.from_number,
                external_call_id=start.call_sid,
            )
            account_sid = env.get("TWILIO_ACCOUNT_SID", "").strip()
            auth_token = env.get("TWILIO_AUTH_TOKEN", "").strip()
            serializer = TwilioFrameSerializer(
                stream_sid=start.stream_sid,
                call_sid=start.call_sid,
                account_sid=account_sid or None,
                auth_token=auth_token or None,
                params=TwilioFrameSerializer.InputParams(
                    auto_hang_up=bool(account_sid and auth_token),
                    sample_rate=8000,
                ),
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
            )
        except WebSocketDisconnect:
            return
        except Exception as error:
            logger.exception("Twilio media stream failed: {}", error)
            try:
                await websocket.close(code=1011)
            except RuntimeError:
                pass
        finally:
            if websocket.client_state.value != 3:
                try:
                    await websocket.close()
                except RuntimeError:
                    pass

    return app


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


def _websocket_url(base_url: str) -> str:
    if base_url.startswith("https://"):
        return "wss://" + base_url[len("https://") :].rstrip("/")
    if base_url.startswith("http://"):
        return "ws://" + base_url[len("http://") :].rstrip("/")
    return base_url.rstrip("/")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the inbound Twilio voice server.")
    parser.add_argument("--host", default="0.0.0.0")
    parser.add_argument("--port", type=int, default=7861)
    return parser


def main() -> None:
    load_dotenv(VOICE_ROOT / ".env")
    args = build_parser().parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
