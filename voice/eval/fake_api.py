from __future__ import annotations

import argparse
import hmac
from pathlib import Path
from uuid import UUID, uuid4

import uvicorn
from fastapi import FastAPI, Header, HTTPException, Query
from inbound_core.call_report import CallReport, CallStart, RouteResponse

from inbound_voice.config import load_assistant_config


def create_fake_api(
    route_response: RouteResponse,
    token: str,
    *,
    fail_reports: int = 0,
    to_number: str | None = None,
) -> FastAPI:
    app = FastAPI()
    app.state.fake = {
        "token": token,
        "route_response": route_response,
        "to_number": to_number,
        "fail_reports": fail_reports,
        "calls": [],
        "reports": [],
    }

    async def require_token(x_service_token: str | None) -> None:
        if not x_service_token or not hmac.compare_digest(x_service_token, app.state.fake["token"]):
            raise HTTPException(status_code=401, detail="Invalid service token")

    @app.get("/internal/voice/route", response_model=RouteResponse, dependencies=[])
    async def route_call(
        to: str = Query(...),
        from_number: str | None = Query(default=None, alias="from"),
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> RouteResponse:
        await require_token(x_service_token)
        expected = app.state.fake["to_number"]
        if expected is not None and to != expected:
            raise HTTPException(status_code=404, detail="No configured destination")
        return app.state.fake["route_response"]

    @app.post(
        "/internal/voice/calls",
        status_code=201,
        dependencies=[],
    )
    async def start_call(
        payload: CallStart,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> dict[str, UUID]:
        await require_token(x_service_token)
        conversation_id = uuid4()
        app.state.fake["calls"].append(
            {"conversation_id": str(conversation_id), **payload.model_dump(mode="json")}
        )
        return {"conversation_id": conversation_id}

    @app.post(
        "/internal/voice/calls/{conversation_id}/report",
        status_code=202,
        dependencies=[],
    )
    async def post_report(
        conversation_id: UUID,
        payload: CallReport,
        x_service_token: str | None = Header(default=None, alias="X-Service-Token"),
    ) -> dict[str, bool]:
        await require_token(x_service_token)
        if app.state.fake["fail_reports"] > 0:
            app.state.fake["fail_reports"] -= 1
            raise HTTPException(status_code=503, detail="Configured fake report failure")
        app.state.fake["reports"].append(
            {
                "conversation_id": str(conversation_id),
                **payload.model_dump(mode="json"),
            }
        )
        return {"accepted": True}

    @app.get("/_fake/state")
    async def state() -> dict:
        return {
            "calls": app.state.fake["calls"],
            "reports": app.state.fake["reports"],
        }

    return app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run an in-memory fake inbound voice API.")
    parser.add_argument("--port", type=int, default=8010)
    parser.add_argument("--token", default="dev-token")
    parser.add_argument("--to", default="+441234567890")
    parser.add_argument(
        "--assistant",
        type=Path,
        default=Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml",
    )
    parser.add_argument("--context-card")
    return parser


def main() -> None:
    args = build_parser().parse_args()
    assistant = load_assistant_config(args.assistant)
    route_response = RouteResponse(
        tenant_id=UUID("10000000-0000-0000-0000-000000000001"),
        assistant_id=UUID("20000000-0000-0000-0000-000000000002"),
        config=assistant,
        digest=assistant.knowledge.digest_content,
        context_card=args.context_card,
    )
    app = create_fake_api(route_response, args.token, to_number=args.to)
    uvicorn.run(app, host="0.0.0.0", port=args.port)


if __name__ == "__main__":
    main()
