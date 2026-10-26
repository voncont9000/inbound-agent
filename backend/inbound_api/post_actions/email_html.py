from __future__ import annotations

import re
from html import escape
from urllib.parse import urlsplit

_URL = re.compile(r"https?://[^\s<>\"']+", re.IGNORECASE)
_DETAIL_LINE = re.compile(r"^([^:\n]{1,40}): (.+)$")


def render_email_html(*, body_text: str, business_name: str, footer_label: str = "Sent by") -> str:
    paragraphs = [
        paragraph for paragraph in re.split(r"\n[ \t]*\n", body_text.strip()) if paragraph.strip()
    ]
    rendered_paragraphs = []
    for paragraph in paragraphs:
        stripped = paragraph.strip()
        url_match = _URL.fullmatch(stripped)
        if url_match and url_match.group() == _trim_url(url_match.group()):
            url = url_match.group()
            label = "Open conversation" if "/conversations/" in url else url
            rendered_paragraphs.append(
                '<p style="margin:0 0 20px;text-align:center">'
                f'<a href="{escape(url, quote=True)}" '
                'style="display:inline-block;padding:12px 20px;border-radius:6px;'
                'background:#2457d6;color:#ffffff;text-decoration:none;font-weight:600">'
                f"{escape(label)}</a></p>"
            )
            continue

        detail_rows = [_DETAIL_LINE.fullmatch(line) for line in paragraph.splitlines()]
        if len(detail_rows) >= 2 and all(detail_rows):
            rows = "".join(
                "<tr>"
                f'<th width="35%" align="left" valign="top" style="padding:8px 12px;'
                f"border-bottom:1px solid #e5e7eb;color:#4b5563;font-weight:600;"
                f'vertical-align:top">{escape(match.group(1))}</th>'
                f'<td valign="top" style="padding:8px 12px;border-bottom:1px solid #e5e7eb;'
                f'vertical-align:top">'
                f"{_linkify(match.group(2))}</td>"
                "</tr>"
                for match in detail_rows
                if match is not None
            )
            rendered_paragraphs.append(
                '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
                'style="margin:0 0 20px;border-collapse:collapse">'
                f"<tbody>{rows}</tbody></table>"
            )
            continue

        rendered_paragraphs.append(
            '<p style="margin:0 0 20px;line-height:1.6;color:#283044">'
            f"{_render_paragraph(paragraph)}</p>"
        )

    content = "".join(rendered_paragraphs)
    escaped_business_name = escape(business_name)
    escaped_footer_label = escape(footer_label)
    return (
        '<!doctype html><html><body style="margin:0;padding:24px 12px;'
        "background:#f3f4f6;font-family:-apple-system,BlinkMacSystemFont,Segoe UI,"
        'Roboto,Helvetica,Arial,sans-serif;color:#283044">'
        '<table role="presentation" width="100%" cellspacing="0" cellpadding="0" '
        'style="border-collapse:collapse"><tr><td align="center">'
        '<table role="presentation" width="600" cellspacing="0" cellpadding="0" '
        'style="width:100%;max-width:600px;border-collapse:collapse;background:#ffffff;'
        'border-radius:10px;overflow:hidden"><tr><td style="padding:28px 32px">'
        '<div style="margin:0 0 24px;font-size:20px;font-weight:700;color:#172033">'
        f"{escaped_business_name}</div>"
        f"{content}"
        '<div style="padding-top:8px;border-top:1px solid #e5e7eb;color:#6b7280;'
        f'font-size:12px">{escaped_footer_label} '
        f"{escaped_business_name}</div>"
        "</td></tr></table></td></tr></table></body></html>"
    )


def _render_paragraph(paragraph: str) -> str:
    lines = []
    for line in paragraph.splitlines():
        detail = _DETAIL_LINE.fullmatch(line)
        if detail:
            lines.append(f"<strong>{escape(detail.group(1))}:</strong> {_linkify(detail.group(2))}")
        else:
            lines.append(_linkify(line))
    return "<br>".join(lines)


def _linkify(text: str) -> str:
    parts = []
    position = 0
    for match in _URL.finditer(text):
        raw_url = match.group()
        url = _trim_url(raw_url)
        parsed = urlsplit(url)
        if not url or parsed.scheme.lower() not in {"http", "https"} or not parsed.netloc:
            continue
        url_end = match.start() + len(url)
        parts.append(escape(text[position : match.start()]))
        escaped_url = escape(url, quote=True)
        parts.append(
            f'<a href="{escaped_url}" style="color:#2457d6;text-decoration:underline">'
            f"{escape(url)}</a>"
        )
        position = url_end
    parts.append(escape(text[position:]))
    return "".join(parts)


def _trim_url(url: str) -> str:
    trimmed = url.rstrip(".,!?;:")
    while trimmed.endswith(")") and trimmed.count(")") > trimmed.count("("):
        trimmed = trimmed[:-1]
    return trimmed
