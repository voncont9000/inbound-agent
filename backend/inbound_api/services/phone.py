import phonenumbers
from phonenumbers import PhoneNumberFormat


def normalise_phone(raw: str, region: str = "GB", *, allow_possible: bool = False) -> str:
    try:
        number = phonenumbers.parse(raw, region)
    except phonenumbers.NumberParseException as error:
        raise ValueError("Invalid phone number") from error
    if not phonenumbers.is_valid_number(number) and not (
        allow_possible and phonenumbers.is_possible_number(number)
    ):
        raise ValueError("Invalid phone number")
    return phonenumbers.format_number(number, PhoneNumberFormat.E164)
