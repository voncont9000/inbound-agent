from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from inbound_core.call_report import CallStart, RouteResponse
from loguru import logger

from inbound_voice.api_client import (
    DEFAULT_SPOOL_DIR,
    VoiceApiClient,
    api_settings_from_env,
)
from inbound_voice.config import AssistantConfig, load_assistant_config

VOICE_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_ASSISTANT_CONFIG = VOICE_ROOT / "config" / "assistants" / "demo_firm.yaml"


@dataclass
class PreparedCall:
    assistant: AssistantConfig
    context_card: str | None
    api: VoiceApiClient | None
    call_start: CallStart | None
    route: RouteResponse | None


async def prepare_call(
    env: Mapping[str, str],
    *,
    channel: str,
    to_number: str | None,
    from_number: str | None,
    external_call_id: str,
    web_call_token: str | None = None,
) -> PreparedCall:
    if channel not in {"phone", "web"}:
        raise ValueError("channel must be 'phone' or 'web'")

    destination = env.get("WEB_CALL_TO_NUMBER", "").strip() if channel == "web" else to_number
    settings = api_settings_from_env(env)
    if channel == "web" and web_call_token is not None:
        if settings is None:
            raise RuntimeError(
                "API_BASE_URL and VOICE_SERVICE_TOKEN are required for tokenized web calls."
            )
        api = VoiceApiClient(
            settings,
            spool_dir=env.get("VOICE_SPOOL_DIR", str(DEFAULT_SPOOL_DIR)),
        )
        call_start = CallStart(
            to_number=destination or "",
            from_number=from_number,
            external_call_id=external_call_id,
            channel=channel,
            started_at=datetime.now(UTC),
            web_call_token=web_call_token,
        )
        try:
            route = await api.route_web_call(web_call_token)
        except Exception as error:
            raise RuntimeError("Could not route tokenized web call.") from error
        assistant = route.config
        if route.digest is not None:
            assistant = assistant.model_copy(
                update={
                    "knowledge": assistant.knowledge.model_copy(
                        update={"digest_content": route.digest}
                    )
                }
            )
        return PreparedCall(
            assistant=assistant,
            context_card=None,
            api=api,
            call_start=call_start,
            route=route,
        )
    if settings is None or (channel == "web" and not destination):
        if settings is not None and channel == "web" and not destination:
            logger.warning("WEB_CALL_TO_NUMBER is missing; using local assistant configuration.")
        return PreparedCall(
            assistant=_load_local_config(env),
            context_card=None,
            api=None,
            call_start=None,
            route=None,
        )

    api = VoiceApiClient(
        settings,
        spool_dir=env.get("VOICE_SPOOL_DIR", str(DEFAULT_SPOOL_DIR)),
    )
    call_start = CallStart(
        to_number=destination or "",
        from_number=from_number,
        external_call_id=external_call_id,
        channel=channel,
        started_at=datetime.now(UTC),
    )
    try:
        route = await api.route(destination or "", from_number)
        assistant = route.config
        if route.digest is not None:
            assistant = assistant.model_copy(
                update={
                    "knowledge": assistant.knowledge.model_copy(
                        update={"digest_content": route.digest}
                    )
                }
            )
        return PreparedCall(
            assistant=assistant,
            context_card=route.context_card,
            api=api,
            call_start=call_start,
            route=route,
        )
    except Exception as error:
        logger.warning(
            "Voice API route lookup failed; using local assistant configuration: {}", error
        )
        return PreparedCall(
            assistant=_load_local_config(env),
            context_card=None,
            api=api,
            call_start=call_start,
            route=None,
        )


def _load_local_config(env: Mapping[str, str]) -> AssistantConfig:
    assistant_path = Path(env.get("ASSISTANT_CONFIG", str(DEFAULT_ASSISTANT_CONFIG)))
    return load_assistant_config(assistant_path)
