import asyncio

import pytest

from inbound_voice.phone_speech import PhoneNumberSpeechTransform


def _transform(text: str, region: str | None = "GB") -> str:
    return asyncio.run(PhoneNumberSpeechTransform(region)(text, "sentence"))


def test_international_phone_number_is_spoken_in_formatted_groups() -> None:
    assert _transform("+447716131661") == "+4 4, 7 7 1 6, 1 3 1 6 6 1"


def test_national_phone_number_uses_the_locale_region() -> None:
    assert _transform("Call 020 4652 5154 today.") == ("Call 0 2 0, 4 6 5 2, 5 1 5 4 today.")


@pytest.mark.parametrize(
    "text",
    [
        "£500,000",
        "2026",
        "10:30",
        "1234",
        "W1, W2 W9 and NW8",
        "W11 3QX",
        "Monday 27 October",
        "Email 02046525154@example.com",
    ],
)
def test_non_phone_numbers_and_email_addresses_are_unchanged(text: str) -> None:
    assert _transform(text) == text


def test_without_a_region_only_international_numbers_are_transformed() -> None:
    assert _transform("020 4652 5154", None) == "020 4652 5154"
    assert _transform("+447716131661", None) == "+4 4, 7 7 1 6, 1 3 1 6 6 1"


def test_unparseable_phone_number_is_unchanged() -> None:
    assert _transform("+9991234567") == "+9991234567"
