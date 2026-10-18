import re

from inbound_core.config import PronunciationEntry
from pipecat.frames.frames import AggregationType


class PronunciationTransform:
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

    async def __call__(self, text: str, aggregation_type: AggregationType | str) -> str:
        if self._pattern is None:
            return text
        return self._pattern.sub(lambda match: self._say_as[match.group().casefold()], text)
