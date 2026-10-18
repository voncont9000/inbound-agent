import asyncio

from inbound_core.config import PronunciationEntry

from inbound_voice.pronunciation import PronunciationFilter


def test_pronunciation_filter_matches_whole_terms_case_insensitively() -> None:
    pronunciation_filter = PronunciationFilter([PronunciationEntry(term="ACAS", say_as="Ay-cass")])

    result = asyncio.run(
        pronunciation_filter.filter("ACAS, acas! XACAS and ACASual are unchanged.")
    )

    assert result == "Ay-cass, Ay-cass! XACAS and ACASual are unchanged."


def test_pronunciation_filter_prefers_longest_terms_and_handles_punctuation() -> None:
    pronunciation_filter = PronunciationFilter(
        [
            PronunciationEntry(term="York", say_as="Yark"),
            PronunciationEntry(term="New York", say_as="New Yawk"),
            PronunciationEntry(term="R&D", say_as="research and development"),
        ]
    )

    result = asyncio.run(pronunciation_filter.filter('New York; York. "R&D," and R&D-team.'))

    assert result == (
        'New Yawk; Yark. "research and development," and research and development-team.'
    )
