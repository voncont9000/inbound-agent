import asyncio
from typing import Any
from urllib.parse import urlsplit

from inbound_core.knowledge import (
    crawl_websites,
    documents_for_prompt,
    response_usage,
)
from inbound_core.pricing import load_price_table
from inbound_core.setup_draft import build_setup_draft_prompt, parse_setup_draft
from inbound_core.templates import find_template
from sqlalchemy.ext.asyncio import AsyncSession

from inbound_api.jobs import PermanentJobError, register
from inbound_api.services.knowledge_import import make_genai_client
from inbound_api.services.url_safety import is_public_address, is_public_http_url
from inbound_api.settings import get_settings


def _load_template(template_name: str):
    settings = get_settings()
    try:
        template = find_template(settings.templates_dir, template_name)
    except (OSError, ValueError) as error:
        raise PermanentJobError("Could not load assistant template") from error
    if template is None:
        raise PermanentJobError("Unknown template")
    return template.config


def _setup_cost(settings, model: str, usage: dict[str, int]) -> float | None:
    try:
        price_table = load_price_table(settings.templates_dir.parent / "prices.yaml")
        model_prices = price_table["prices"]["llm_models"].get(model)
        if not model_prices:
            return None
        input_rate = model_prices.get("input_per_1m_tokens", {}).get("value")
        output_rate = model_prices.get("output_per_1m_tokens", {}).get("value")
        if input_rate is None or output_rate is None:
            return None
        return float(
            usage["input_tokens"] * float(input_rate) / 1_000_000
            + usage["output_tokens"] * float(output_rate) / 1_000_000
        )
    except (KeyError, OSError, TypeError, ValueError):
        return None


@register("assistant_setup_draft")
async def assistant_setup_draft(_session: AsyncSession, payload: dict[str, Any]) -> dict[str, Any]:
    settings = get_settings()
    template = _load_template(payload["template"])
    website_url = payload["website_url"]
    parsed_url = urlsplit(website_url)
    if parsed_url.scheme.casefold() not in {"http", "https"} or not parsed_url.hostname:
        raise PermanentJobError("Website URL must be HTTP(S) with a host.")
    allow_url = None if settings.knowledge_allow_private_urls else is_public_http_url
    allow_address = None if settings.knowledge_allow_private_urls else is_public_address
    records = await asyncio.to_thread(
        crawl_websites,
        [website_url],
        max_pages=min(settings.knowledge_max_pages, settings.setup_draft_max_pages),
        request_delay=settings.knowledge_request_delay,
        allow_url=allow_url,
        allow_address=allow_address,
    )
    if not any(
        isinstance(record.get("text"), str) and record["text"].strip() for record in records
    ):
        raise PermanentJobError("The website returned no readable text.")
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        raise PermanentJobError("GOOGLE_API_KEY is not configured.")

    client = make_genai_client(api_key)
    try:
        response = await client.aio.models.generate_content(
            model=settings.setup_draft_model,
            contents=build_setup_draft_prompt(
                payload["business_name"], template, documents_for_prompt(records)
            ),
            config={"response_mime_type": "application/json"},
        )
    finally:
        await client.aio.aclose()

    draft, warnings = parse_setup_draft(response.text or "", template)
    usage = response_usage(response)
    return {
        "draft": draft.model_dump(mode="json"),
        "warnings": warnings,
        "pages": [
            {key: value for key, value in record.items() if key != "text"} for record in records
        ],
        "model": settings.setup_draft_model,
        "usage": usage,
        "cost_usd": _setup_cost(settings, settings.setup_draft_model, usage),
    }
