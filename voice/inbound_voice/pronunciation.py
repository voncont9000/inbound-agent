import re
from collections.abc import Mapping
from typing import Any

from inbound_core.config import PronunciationEntry
from pipecat.utils.text.base_text_filter import BaseTextFilter


class PronunciationFilter(BaseTextFilter):
    def __init__(self, pronunciations: list[PronunciationEntry]) -> None:
        self._say_as = {entry.term.casefold(): entry.say_as for entry in pronunciations}
        terms = sorted((entry.term for entry in pronunciations), key=len, reverse=True)
        self._pattern = (
            re.compile(
                r"(?<!\w)(?:" + "|".join(re.escape(term) for term in terms) + r")(?!\w)",
                re.IGNORECASE,
            )
            if terms
            else None
        )

    async def update_settings(self, settings: Mapping[str, Any]) -> None:
        return None

    async def filter(self, text: str) -> str:
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda match: self._say_as[match.group().casefold()], text)

    async def handle_interruption(self) -> None:
        return None

    async def reset_interruption(self) -> None:
        return None
