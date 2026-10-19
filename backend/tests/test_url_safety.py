import pytest

from inbound_api.services.url_safety import normalize_website_url


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("herefordssolicitors.co.uk", "https://herefordssolicitors.co.uk"),
        ("  www.x.co.uk/about ", "https://www.x.co.uk/about"),
        ("http://x.com", "http://x.com"),
        ("HTTPS://x.com", "HTTPS://x.com"),
        ("//x.com", "https://x.com"),
        ("", ""),
    ],
)
def test_normalize_website_url(value: str, expected: str) -> None:
    assert normalize_website_url(value) == expected
