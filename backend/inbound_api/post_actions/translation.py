from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from typing import Any

from inbound_api.services.knowledge_import import make_genai_client
from inbound_api.settings import Settings

LOGGER = logging.getLogger(__name__)


@dataclass(frozen=True)
class TranslatedEmail:
    subject: str
    body: str
    footer_label: str


async def translate_email(
    *,
    subject: str,
    body: str,
    footer_label: str,
    target_language: str,
    settings: Settings,
) -> TranslatedEmail | None:
    api_key = (settings.google_api_key or "").strip()
    if not api_key:
        LOGGER.warning("Customer email translation skipped because no API key is configured")
        return None
    if not body:
        LOGGER.warning("Customer email translation skipped because the rendered body is empty")
        return None

    client: Any = None
    translated_email: TranslatedEmail | None = None
    try:
        client = make_genai_client(api_key)
        prompt = (
            f"Translate the rendered customer email into {target_language}. If it is already in "
            "that language, preserve it as-is. Translate the subject, body, and footer label. "
            "Keep person names, business names, email addresses, phone numbers, URLs, dates, "
            "times, amounts, and reference numbers exactly unchanged. Preserve line breaks and "
            "list markers. Add nothing, remove nothing, and explain nothing. Return only JSON "
            "with exactly the string fields subject, body, and footer_label.\n\n"
        )
        email_json = json.dumps(
            {"subject": subject, "body": body, "footer_label": footer_label},
            ensure_ascii=False,
        )
        prompt += email_json
        async with asyncio.timeout(10):
            response = await client.aio.models.generate_content(
                model=settings.text_agent_model,
                contents=prompt,
                config={
                    "response_mime_type": "application/json",
                    "response_schema": {
                        "type": "OBJECT",
                        "properties": {
                            "subject": {"type": "STRING"},
                            "body": {"type": "STRING"},
                            "footer_label": {"type": "STRING"},
                        },
                        "required": ["subject", "body", "footer_label"],
                    },
                },
            )
        translated = json.loads(response.text or "{}")
        if not isinstance(translated, dict):
            raise ValueError("Translation response must be an object")
        translated_subject = translated.get("subject")
        translated_body = translated.get("body")
        translated_footer_label = translated.get("footer_label")
        if not all(
            isinstance(value, str) and value.strip()
            for value in (translated_subject, translated_body, translated_footer_label)
        ):
            LOGGER.warning("Customer email translation returned an empty field")
        else:
            body_ratio = len(translated_body) / len(body)
            if not 0.5 <= body_ratio <= 2.0:
                LOGGER.warning("Customer email translation returned an implausible body length")
            else:
                translated_email = TranslatedEmail(
                    subject=translated_subject,
                    body=translated_body,
                    footer_label=translated_footer_label,
                )
    except TimeoutError:
        LOGGER.warning("Customer email translation timed out")
        return None
    except Exception:
        LOGGER.warning("Customer email translation failed")
        return None
    finally:
        if client is not None:
            try:
                await client.aio.aclose()
            except Exception:
                LOGGER.warning("Customer email translation client could not be closed")
                translated_email = None
    return translated_email
