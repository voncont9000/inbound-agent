import pytest
from fastapi import HTTPException

from inbound_api.services.diff import config_diff
from inbound_api.services.pagination import decode_cursor, encode_cursor
from inbound_api.services.phone import normalise_phone
from inbound_api.services.review import field_needs_review, merged_needs_review


def test_phone_normalisation_requires_valid_number() -> None:
    assert normalise_phone("020 7946 0000") == "+442079460000"
    with pytest.raises(ValueError):
        normalise_phone("not a phone number")


def test_review_rule_ignores_human_and_matches_configured_threshold() -> None:
    assert field_needs_review({"confidence": 0.69}, 0.7)
    assert field_needs_review({"needs_review": True, "status": "model"}, 0.7)
    assert not field_needs_review({"confidence": 0.7}, 0.7)
    assert not field_needs_review({"needs_review": True, "status": "human"}, 0.7)
    assert merged_needs_review(
        {"fields": {"name": {"confidence": 0.2}, "email": {"status": "human"}}}, 0.7
    )


def test_config_diff_is_dotted_and_deterministic() -> None:
    assert config_diff(
        {"nested": {"b": 1, "a": 2}, "list": [1]},
        {"nested": {"b": 2, "c": 3}, "list": [1, 2]},
    ) == {
        "nested.a": {"from": 2, "to": None},
        "nested.b": {"from": 1, "to": 2},
        "nested.c": {"from": None, "to": 3},
        "list": {"from": [1], "to": [1, 2]},
    }


def test_cursor_round_trip_and_invalid_cursor() -> None:
    from datetime import UTC, datetime
    from uuid import uuid4

    row_id = uuid4()
    created_at = datetime.now(UTC)
    assert decode_cursor(encode_cursor(created_at, row_id)) == (created_at, row_id)
    with pytest.raises(HTTPException) as error:
        decode_cursor("invalid")
    assert error.value.status_code == 400
