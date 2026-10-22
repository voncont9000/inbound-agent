from __future__ import annotations

import re

import phonenumbers
from phonenumbers import PhoneNumberFormat
from pipecat.frames.frames import AggregationType

PHONE_NUMBER_PATTERN = re.compile(r"(?<![\w+£$€])\+?\d[\d \-()]{5,}\d(?![\w,.]?\d)")
EMAIL_ADDRESS_PATTERN = re.compile(
    r"(?<![\w.!#$%&'*+/=?^`{|}~-])[\w.!#$%&'*+/=?^`{|}~-]+@"
    r"[A-Za-z0-9](?:[A-Za-z0-9.-]*[A-Za-z0-9])?"
)


class PhoneNumberSpeechTransform:
    def __init__(self, region: str | None) -> None:
        self._region = region

    async def __call__(self, text: str, aggregation_type: AggregationType | str) -> str:
        email_spans = [match.span() for match in EMAIL_ADDRESS_PATTERN.finditer(text)]

        def transform(match: re.Match[str]) -> str:
            if any(
                match.start() < email_end and match.end() > email_start
                for email_start, email_end in email_spans
            ):
                return match.group()
            return _speak_phone_number(match.group(), self._region)

        return PHONE_NUMBER_PATTERN.sub(transform, text)


def _speak_phone_number(candidate: str, region: str | None) -> str:
    if sum(character.isdigit() for character in candidate) < 7:
        return candidate
    is_international = candidate.startswith("+")
    if not is_international and region is None:
        return candidate
    try:
        number = phonenumbers.parse(candidate, region)
    except phonenumbers.NumberParseException:
        return candidate
    if not phonenumbers.is_possible_number(number):
        return candidate

    number_format = (
        PhoneNumberFormat.INTERNATIONAL if is_international else PhoneNumberFormat.NATIONAL
    )
    formatted = phonenumbers.format_number(number, number_format)
    spoken_groups = []
    for index, group in enumerate(re.split(r"[\s-]+", formatted)):
        digits = "".join(character for character in group if character.isdigit())
        if digits:
            spoken = " ".join(digits)
            if index == 0 and is_international:
                spoken = f"+{spoken}"
            spoken_groups.append(spoken)
    return ", ".join(spoken_groups) if spoken_groups else candidate
