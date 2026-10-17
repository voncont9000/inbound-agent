import re

_SPOKEN_DIGITS = {
    "oh": "0",
    "o": "0",
    "zero": "0",
    "nought": "0",
    "one": "1",
    "two": "2",
    "three": "3",
    "four": "4",
    "five": "5",
    "six": "6",
    "seven": "7",
    "eight": "8",
    "nine": "9",
}
_TRAILING_PUNCTUATION = ".,!?;:'\"”’)]}"
_CONNECTIVE = re.compile(r"\b(?:and|then|um|uh|er)\s*$", re.IGNORECASE)


def _spoken_digit(token: str) -> str | None:
    return _SPOKEN_DIGITS.get(token.lower(), token if token.isdigit() else None)


def _normalize_spoken_digits(text: str) -> str:
    pattern = re.compile(r"\b(double|triple)\s+([a-z]+|\d+)\b", re.IGNORECASE)

    def expand(match: re.Match[str]) -> str:
        digit = _spoken_digit(match.group(2))
        if digit is None:
            return match.group(0)
        count = 2 if match.group(1).lower() == "double" else 3
        return digit * count

    text = pattern.sub(expand, text.lower())
    return re.sub(
        r"\b(?:oh|o|zero|nought|one|two|three|four|five|six|seven|eight|nine)\b",
        lambda match: _SPOKEN_DIGITS[match.group(0)],
        text,
    )


def _trailing_number_run(text: str) -> str:
    match = re.search(r"([0-9+\s-]+)$", text)
    return match.group(1) if match else ""


def _digits_in(run: str) -> str:
    return re.sub(r"\D", "", run)


def incomplete_phone_number(text: str) -> bool:
    normalized = _normalize_spoken_digits(text).rstrip().rstrip(_TRAILING_PUNCTUATION).rstrip()

    connective = _CONNECTIVE.search(normalized)
    if connective is not None:
        preceding = normalized[: connective.start()].rstrip().rstrip(_TRAILING_PUNCTUATION).rstrip()
        if len(_digits_in(_trailing_number_run(preceding))) >= 4:
            return True

    run = _trailing_number_run(normalized)
    digits = _digits_in(run)
    if len(digits) < 4:
        return False

    compact = re.sub(r"[\s-]", "", run)
    return (compact.startswith("0") and len(digits) < 11) or (
        (compact.startswith("44") or compact.startswith("+44")) and len(digits) < 12
    )
