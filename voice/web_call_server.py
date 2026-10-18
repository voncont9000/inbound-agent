from __future__ import annotations

import argparse
import asyncio
import hmac
import os
from collections.abc import Mapping
from pathlib import Path
from uuid import uuid4

import uvicorn
from dotenv import load_dotenv
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse
from loguru import logger
from pipecat.transports.websocket.fastapi import (
    FastAPIWebsocketParams,
    FastAPIWebsocketTransport,
)
from starlette.websockets import WebSocketState

from bot import resolve_caller_id, run_bot
from inbound_voice.call_setup import prepare_call
from inbound_voice.web_call import PcmWebSocketSerializer

VOICE_ROOT = Path(__file__).resolve().parent
WEB_CALL_HTML = VOICE_ROOT / "inbound_voice" / "static" / "web_call.html"


def create_app(env: Mapping[str, str] = os.environ) -> FastAPI:
    app = FastAPI()
    max_concurrent = int(env.get("WEB_CALL_MAX_CONCURRENT", "2"))
    if max_concurrent < 1:
        raise ValueError("WEB_CALL_MAX_CONCURRENT must be at least 1")
    app.state.active_web_calls = 0
    app.state.web_call_lock = asyncio.Lock()

    @app.get("/")
    async def web_call_page() -> FileResponse:
        return FileResponse(WEB_CALL_HTML, media_type="text/html")

    @app.get("/healthz")
    async def healthz() -> dict[str, bool]:
        return {"ok": True}

    @app.websocket("/ws")
    async def web_call_websocket(websocket: WebSocket) -> None:
        await websocket.accept()
        call_token = websocket.query_params.get("call_token")
        token = env.get("WEB_CALL_TOKEN", "").strip()
        if (
            call_token is None
            and token
            and not hmac.compare_digest(websocket.query_params.get("token", ""), token)
        ):
            await websocket.close(code=1008)
            return

        async with app.state.web_call_lock:
            if app.state.active_web_calls >= max_concurrent:
                await websocket.close(code=1013)
                return
            app.state.active_web_calls += 1

        try:
            caller_id = resolve_caller_id(None, env, telephony=False)
            call_id = str(uuid4())
            prepared = await prepare_call(
                env,
                channel="web",
                to_number=env.get("WEB_CALL_TO_NUMBER"),
                from_number=caller_id,
                external_call_id=call_id,
                web_call_token=call_token,
            )
            transport = FastAPIWebsocketTransport(
                websocket,
                FastAPIWebsocketParams(
                    audio_in_enabled=True,
                    audio_out_enabled=True,
                    audio_in_sample_rate=16000,
                    audio_out_sample_rate=24000,
                    add_wav_header=False,
                    serializer=PcmWebSocketSerializer(),
                ),
            )
            await run_bot(
                transport,
                telephony=False,
                caller_id=caller_id,
                prepared=prepared,
                call_id=call_id,
            )
        except WebSocketDisconnect:
            return
        except Exception as error:
            logger.exception("Browser call WebSocket failed: {}", error)
            try:
                await websocket.close(code=1011)
            except RuntimeError:
                pass
        finally:
            async with app.state.web_call_lock:
                app.state.active_web_calls -= 1
            if websocket.client_state != WebSocketState.DISCONNECTED:
                try:
                    await websocket.close()
                except RuntimeError:
                    pass

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the hosted browser-call server.")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=7862)
    return parser


def main() -> None:
    load_dotenv(VOICE_ROOT / ".env")
    args = build_parser().parse_args()
    uvicorn.run(create_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
