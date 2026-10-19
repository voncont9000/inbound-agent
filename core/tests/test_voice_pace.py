import pytest

from inbound_core.voice_pace import speechify_rate_percent, speechify_voice_wpm


@pytest.mark.parametrize(
    ("voice_id", "speed", "expected"),
    [
        ("alec", 1.0, 29),
        ("alec", 1.2, 54),
        ("sabrina", 1.0, 0),
        ("sabrina", 0.8, -20),
        ("unknown", 1.0, 0),
        ("unknown", 1.1, 10),
        ("unknown", 0.1, -30),
        ("unknown", 2.0, 60),
    ],
)
def test_speechify_rate_percent(voice_id: str, speed: float, expected: int) -> None:
    assert speechify_rate_percent(voice_id, speed) == expected


def test_speechify_voice_wpm_returns_measured_values_or_none() -> None:
    assert speechify_voice_wpm("alec") == 136
    assert speechify_voice_wpm("unknown") is None
