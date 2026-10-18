from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from inbound_core.config import AssistantConfig, OfficeHours, TransferTarget
from inbound_core.transfer import available_transfer_targets, is_within_office_hours

BASE_CONFIG = {
    "business_name": "Example",
    "greeting": "Hello",
    "office_hours": {
        "days": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday"],
        "opens": "09:00",
        "closes": "17:30",
        "timezone": "Europe/London",
    },
    "price_list": [],
    "policies": [],
    "contacts": {},
    "services": {},
    "urgency_rules": [],
    "live_actions": ["transfer_call"],
    "transfer_targets": [
        {
            "key": "duty_solicitor",
            "label": "Duty solicitor",
            "phone": "07700 900123",
            "when_to_use": "The caller asks to speak to a person now.",
            "office_hours_only": True,
        }
    ],
}


def _config(**overrides) -> AssistantConfig:
    return AssistantConfig.model_validate({**BASE_CONFIG, **overrides})


def test_transfer_target_normalises_possible_phone_and_rejects_invalid() -> None:
    target = TransferTarget.model_validate(
        {
            "key": "duty_solicitor",
            "label": "Duty solicitor",
            "phone": "07700 900123",
            "when_to_use": "When requested.",
        }
    )

    assert target.phone == "+447700900123"
    with pytest.raises(ValidationError, match="Transfer phone must be a valid phone number"):
        TransferTarget.model_validate(
            {
                "key": "duty_solicitor",
                "label": "Duty solicitor",
                "phone": "not a number",
                "when_to_use": "When requested.",
            }
        )


def test_transfer_target_keys_must_be_unique() -> None:
    target = BASE_CONFIG["transfer_targets"][0]
    with pytest.raises(ValidationError, match="Transfer target keys must be unique"):
        _config(transfer_targets=[target, {**target, "label": "Second"}])


def test_office_hours_converts_timezone_and_excludes_close_and_weekends() -> None:
    hours = OfficeHours.model_validate(BASE_CONFIG["office_hours"])

    assert is_within_office_hours(hours, datetime(2026, 7, 20, 8, 30, tzinfo=UTC))
    assert not is_within_office_hours(hours, datetime(2026, 7, 20, 16, 30, tzinfo=UTC))
    assert not is_within_office_hours(hours, datetime(2026, 7, 18, 10, 0, tzinfo=UTC))


def test_available_transfer_targets_require_enabled_action_and_open_hours() -> None:
    target = BASE_CONFIG["transfer_targets"][0]
    before_open = datetime(2026, 7, 20, 7, 30, tzinfo=UTC)

    assert (
        available_transfer_targets(
            _config(live_actions=[]), datetime(2026, 7, 20, 10, 0, tzinfo=UTC)
        )
        == []
    )
    assert available_transfer_targets(_config(transfer_targets=[]), before_open) == []
    assert available_transfer_targets(_config(), before_open) == []
    assert [
        item.key
        for item in available_transfer_targets(_config(), datetime(2026, 7, 20, 8, 30, tzinfo=UTC))
    ] == [target["key"]]
    assert [
        item.key
        for item in available_transfer_targets(_config(), before_open)
        if not item.office_hours_only
    ] == []
