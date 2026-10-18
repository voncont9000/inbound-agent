from inbound_core.phone import normalise_phone as _normalise_phone


def normalize_phone(raw: str | None) -> str | None:
    return _normalise_phone(raw, strict=False)


def normalize_email(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip().lower()
    return value if value and "@" in value else None


def normalize_whatsapp(raw: str | None) -> str | None:
    if raw is None:
        return None
    value = raw.strip()
    if value[:10].lower() == "whatsapp:":
        value = value[10:].strip()
    return normalize_phone(value)
