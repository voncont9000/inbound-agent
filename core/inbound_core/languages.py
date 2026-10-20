from dataclasses import dataclass
from typing import Literal

Locale = Literal[
    "en-GB",
    "en-US",
    "de-DE",
    "es-ES",
    "es-MX",
    "fr-FR",
    "it-IT",
    "pt-BR",
    "multi",
]


@dataclass(frozen=True)
class LocaleInfo:
    label: str
    prompt_name: str
    base: str
    speechify_language: str | None
    speechify_model: str
    accent: str


LOCALES: dict[Locale, LocaleInfo] = {
    "en-GB": LocaleInfo("English (UK)", "British English", "en", "en-GB", "simba-3.2", "British"),
    "en-US": LocaleInfo("English (US)", "American English", "en", "en-US", "simba-3.2", "American"),
    "de-DE": LocaleInfo("German (Germany)", "German", "de", "de-DE", "simba-3.0", "German"),
    "es-ES": LocaleInfo(
        "Spanish (Spain)", "Spanish (Spain)", "es", "es-ES", "simba-3.0", "Spanish"
    ),
    "es-MX": LocaleInfo(
        "Spanish (Mexico)", "Mexican Spanish", "es", "es-MX", "simba-3.0", "Mexican Spanish"
    ),
    "fr-FR": LocaleInfo("French (France)", "French", "fr", "fr-FR", "simba-3.0", "French"),
    "it-IT": LocaleInfo("Italian (Italy)", "Italian", "it", "it-IT", "simba-3.0", "Italian"),
    "pt-BR": LocaleInfo(
        "Portuguese (Brazil)",
        "Brazilian Portuguese",
        "pt",
        "pt-BR",
        "simba-3.0",
        "Brazilian Portuguese",
    ),
    "multi": LocaleInfo(
        "Multilingual", "multiple languages", "multi", None, "simba-3.0", "Multilingual"
    ),
}

DEFAULT_LOCALE: Locale = "en-GB"
DEFAULT_VOICE_ID = "beatrice_32"
MULTI_LANGUAGES = ("en", "de", "es", "fr", "it", "pt")

_LEGACY_LOCALES: dict[str, Locale] = {
    "english": "en-GB",
    "british english": "en-GB",
    "uk english": "en-GB",
    "en": "en-GB",
    "en-gb": "en-GB",
    "en_gb": "en-GB",
    "american english": "en-US",
    "us english": "en-US",
    "en-us": "en-US",
    "german": "de-DE",
    "deutsch": "de-DE",
    "de": "de-DE",
    "de-de": "de-DE",
    "spanish": "es-ES",
    "español": "es-ES",
    "es": "es-ES",
    "es-es": "es-ES",
    "mexican spanish": "es-MX",
    "es-mx": "es-MX",
    "french": "fr-FR",
    "français": "fr-FR",
    "fr": "fr-FR",
    "fr-fr": "fr-FR",
    "italian": "it-IT",
    "it": "it-IT",
    "it-it": "it-IT",
    "portuguese": "pt-BR",
    "brazilian portuguese": "pt-BR",
    "pt": "pt-BR",
    "pt-br": "pt-BR",
    "multi": "multi",
    "multilingual": "multi",
    "auto": "multi",
}


def normalize_locale(value: object) -> Locale:
    """Map stored locale names and tags to supported locales; unknown values default to en-GB."""
    if not isinstance(value, str):
        return DEFAULT_LOCALE
    normalized = " ".join(value.strip().casefold().split())
    if normalized in LOCALES:
        return normalized  # type: ignore[return-value]
    return _LEGACY_LOCALES.get(normalized, DEFAULT_LOCALE)


def spoken_language_instruction(locale: Locale) -> str:
    if locale == "multi":
        return (
            "Speak the language the caller uses, and switch with them if they change language; "
            "if unsure, use English."
        )
    return (
        f"Speak {LOCALES[locale].prompt_name}, even if the instructions, greeting or knowledge "
        "below are written in another language. If the caller clearly switches to another "
        "language, follow them."
    )


def written_language_instruction(locale: Locale) -> str:
    if locale == "multi":
        return "Reply in the language the customer writes in."
    return f"Write in {LOCALES[locale].prompt_name}."


def caller_language_name(locale: Locale) -> str:
    return LOCALES[locale].prompt_name
