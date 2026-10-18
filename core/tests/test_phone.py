from inbound_core.phone import normalise_phone


def test_possible_but_not_valid_number_is_accepted_only_when_not_strict() -> None:
    assert normalise_phone("07700 900456") == "+447700900456"
    assert normalise_phone("07700 900456", strict=True) is None


def test_valid_number_is_accepted_in_both_modes() -> None:
    assert normalise_phone("+441234567890") == "+441234567890"
    assert normalise_phone("+441234567890", strict=True) == "+441234567890"


def test_garbage_is_rejected() -> None:
    assert normalise_phone("not a phone number") is None
