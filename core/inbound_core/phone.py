from __future__ import annotations

import phonenumbers
from phonenumbers import PhoneNumberFormat


def normalise_phone(raw: str | None, region: str = "GB", *, strict: bool = False) -> str | None:
    if raw is None or not raw.strip():
        return None
    try:
        number = phonenumbers.parse(raw.strip(), region)
    except phonenumbers.NumberParseException:
        return None
    is_acceptable = (
        phonenumbers.is_valid_number(number) if strict else phonenumbers.is_possible_number(number)
    )
    if not is_acceptable:
        return None
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)
