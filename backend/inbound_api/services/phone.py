from inbound_core.phone import normalise_phone as _normalise_phone


def normalise_phone(raw: str, region: str = "GB", *, strict: bool = True) -> str:
    normalized = _normalise_phone(raw, region, strict=strict)
    if normalized is None:
        raise ValueError("Invalid phone number")
    return normalized
