import phonenumbers
from phonenumbers import PhoneNumberFormat


def normalise_phone(raw: str, region: str = "GB") -> str:
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException as error:
        raise ValueError("Invalid phone number") from error
    if not phonenumbers.is_valid_number(number):
        raise ValueError("Invalid phone number")
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)
