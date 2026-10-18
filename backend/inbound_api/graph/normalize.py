import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat


def normalize_phone(raw: str | None) -> str | None:
    if not raw or not raw.strip():
        return None
    try:
        number = phonenumbers.parse(raw.strip(), "GB")
    except NumberParseException:
        return None
    if not phonenumbers.is_possible_number(number):
        return None
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)


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
