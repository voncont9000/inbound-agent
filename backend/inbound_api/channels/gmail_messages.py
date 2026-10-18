from __future__ import annotations

import base64
import html
import re
from email.header import decode_header, make_header
from email.utils import parseaddr
from typing import Any

SKIP_LABELS = {
    "SENT",
    "DRAFT",
    "SPAM",
    "TRASH",
    "CATEGORY_PROMOTIONS",
    "CATEGORY_SOCIAL",
    "CATEGORY_UPDATES",
    "CATEGORY_FORUMS",
}
SKIP_LOCAL_PARTS = {
    "noreply",
    "no-reply",
    "donotreply",
    "mailer-daemon",
    "postmaster",
    "notifications",
}
SUPPORTED_ATTACHMENTS = {
    "text/plain": ".txt",
    "text/markdown": ".md",
    "text/csv": ".csv",
    "application/pdf": ".pdf",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}
MAX_ATTACHMENT_BYTES = 2 * 1024 * 1024


def headers_map(message: dict[str, Any]) -> dict[str, str]:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return {}
    headers = payload.get("headers")
    if not isinstance(headers, list):
        return {}
    result: dict[str, str] = {}
    for item in headers:
        if not isinstance(item, dict):
            continue
        name, value = item.get("name"), item.get("value")
        if isinstance(name, str) and isinstance(value, str):
            result[name.casefold()] = str(make_header(decode_header(value))).strip()
    return result


def parse_address(value: str | None) -> tuple[str, str]:
    if not value:
        return "", ""
    name, address = parseaddr(value)
    return name.strip(), address.strip().lower()


def skip_reason(message: dict[str, Any], mailbox: str, settings: dict[str, Any]) -> str | None:
    labels = message.get("labelIds")
    if isinstance(labels, list) and SKIP_LABELS.intersection(labels):
        return "provider_label"
    headers = headers_map(message)
    _, sender = parse_address(headers.get("from"))
    if not sender or "@" not in sender:
        return "invalid_sender"
    mailbox = mailbox.strip().lower()
    mailbox_domain = mailbox.rpartition("@")[2]
    sender_local, _, sender_domain = sender.partition("@")
    if sender == mailbox:
        return "self_sender"
    if sender_domain == mailbox_domain and not settings.get("include_internal", False):
        return "internal_sender"
    if sender_local.casefold() in SKIP_LOCAL_PARTS:
        return "automated_sender"
    if headers.get("list-id") or headers.get("list-unsubscribe"):
        return "mailing_list"
    if headers.get("precedence", "").strip().casefold() in {"bulk", "list", "junk"}:
        return "bulk_precedence"
    auto_submitted = headers.get("auto-submitted", "no").strip().casefold()
    if auto_submitted != "no":
        return "auto_submitted"
    excluded_senders = {str(item).strip().lower() for item in settings.get("exclude_senders", [])}
    excluded_domains = {str(item).strip().lower() for item in settings.get("exclude_domains", [])}
    if sender in excluded_senders:
        return "excluded_sender"
    if sender_domain in excluded_domains:
        return "excluded_domain"
    return None


def strip_quoted_history(text: str) -> str:
    lines = text.splitlines()
    kept: list[str] = []
    for line in lines:
        if re.match(r"^\s*On .{1,300}\s+wrote:\s*$", line, re.IGNORECASE):
            break
        if line.lstrip().startswith(">"):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def _decode_body(value: str) -> str:
    try:
        return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4)).decode(
            "utf-8", errors="replace"
        )
    except (ValueError, TypeError):
        return ""


def message_body(message: dict[str, Any]) -> str:
    payload = message.get("payload")
    if not isinstance(payload, dict):
        return ""
    plain: list[str] = []
    markup: list[str] = []

    def visit(part: dict[str, Any]) -> None:
        mime_type = str(part.get("mimeType", "")).casefold()
        body = part.get("body")
        if isinstance(body, dict) and isinstance(body.get("data"), str):
            decoded = _decode_body(body["data"])
            if mime_type == "text/plain":
                plain.append(decoded)
            elif mime_type == "text/html":
                markup.append(decoded)
        children = part.get("parts")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    visit(payload)
    if plain:
        return strip_quoted_history("\n".join(plain))
    if markup:
        return strip_quoted_history(
            re.sub(r"\s+", " ", html.unescape(re.sub(r"<[^>]+>", " ", "\n".join(markup))))
        )
    body = payload.get("body")
    return (
        strip_quoted_history(_decode_body(body.get("data", ""))) if isinstance(body, dict) else ""
    )


def attachment_parts(message: dict[str, Any]) -> list[dict[str, Any]]:
    payload = message.get("payload")
    found: list[dict[str, Any]] = []

    def visit(part: dict[str, Any]) -> None:
        body = part.get("body")
        if isinstance(body, dict):
            filename = part.get("filename")
            attachment_id = body.get("attachmentId")
            if filename or attachment_id:
                found.append(
                    {
                        "filename": filename or "attachment",
                        "mimeType": part.get("mimeType", ""),
                        "attachmentId": attachment_id,
                        "size": body.get("size"),
                        "data": body.get("data"),
                    }
                )
        children = part.get("parts")
        if isinstance(children, list):
            for child in children:
                if isinstance(child, dict):
                    visit(child)

    if isinstance(payload, dict):
        visit(payload)
    return found


def decode_part_data(value: str) -> bytes:
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))
