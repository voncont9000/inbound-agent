import asyncio

from inbound_core.config import PronunciationEntry

from inbound_voice.pronunciation import PronunciationTransform


def test_pronunciation_transform_matches_whole_terms_case_insensitively() -> None:
    pronunciation_transform = PronunciationTransform(
        [PronunciationEntry(term="ACAS", say_as="Ay-cass")]
    )

    result = asyncio.run(
        pronunciation_transform("ACAS, acas! XACAS and ACASual are unchanged.", "sentence")
    )

    assert result == "Ay-cass, Ay-cass! XACAS and ACASual are unchanged."


def test_pronunciation_transform_prefers_longest_terms_and_handles_punctuation() -> None:
    pronunciation_transform = PronunciationTransform(
        [
            PronunciationEntry(term="York", say_as="Yark"),
            PronunciationEntry(term="New York", say_as="New Yawk"),
            PronunciationEntry(term="R&D", say_as="research and development"),
        ]
    )

    result = asyncio.run(
        pronunciation_transform('New York; York. "R&D," and R&D-team.', "sentence")
    )

    assert result == (
        'New Yawk; Yark. "research and development," and research and development-team.'
    )
