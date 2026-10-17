import pytest

from inbound_voice.turns import incomplete_phone_number


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("my number is 07700 90045", True),
        ("07700 900456", False),
        ("+44 7700 9004", True),
        ("my number is double oh seven seven", True),
        ("I was dismissed on the 10th of August", False),
        ("it cost 250000", False),
        ("price is 595", False),
        ("my number is 07700 90045 and", True),
        ("my number is 07700 90045.", True),
        ("my number is 07. 70. 09.", True),
    ],
)
def test_incomplete_phone_number(text: str, expected: bool) -> None:
    assert incomplete_phone_number(text) is expected
