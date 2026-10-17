from datetime import date
from pathlib import Path

import pytest
from pydantic import ValidationError

from inbound_voice.config import load_assistant_config
from inbound_voice.intake import (
    build_service_model,
    days_until_deadline,
    deadline_from,
)

CONFIG_PATH = Path(__file__).resolve().parents[1] / "config" / "assistants" / "demo_firm.yaml"


def test_dynamic_conveyancing_model_validates_field_types() -> None:
    config = load_assistant_config(CONFIG_PATH)
    model = build_service_model(config.services["conveyancing"])
    intake = model.model_validate(
        {
            "caller_name": "A. Caller",
            "phone": " +44 7700 900123 ",
            "email": "caller@example.com",
            "transaction_type": "purchase",
            "target_exchange_date": "2026-12-01",
        }
    )
    assert intake.transaction_type == "purchase"
    assert intake.target_exchange_date == date(2026, 12, 1)
    assert intake.phone == "+44 7700 900123"


def test_dynamic_service_model_rejects_invalid_enum_and_extra_fields() -> None:
    config = load_assistant_config(CONFIG_PATH)
    model = build_service_model(config.services["conveyancing"])
    with pytest.raises(ValidationError):
        model.model_validate(
            {"transaction_type": "transfer", "unknown": "not allowed"}
        )


def test_dynamic_employment_model_validates_dates_and_enum() -> None:
    config = load_assistant_config(CONFIG_PATH)
    model = build_service_model(config.services["employment"])
    intake = model.model_validate(
        {
            "caller_name": "B. Caller",
            "funding": "private",
            "contact_preference": "email",
            "issue_type": "dismissal",
            "employer_name": "Example Ltd",
            "job_title": "Analyst",
            "length_of_service": "4 years",
            "termination_date": "2026-02-28",
            "last_act_complained_of": "2026-02-25",
            "acas_early_conciliation_started": False,
            "other_parties": ["Example Ltd"],
        }
    )
    assert intake.issue_type == "dismissal"


def test_dynamic_model_rejects_invalid_employment_fields() -> None:
    config = load_assistant_config(CONFIG_PATH)
    model = build_service_model(config.services["employment"])
    with pytest.raises(ValidationError):
        model.model_validate(
            {"issue_type": "pay dispute", "acas_early_conciliation_started": "maybe"}
        )


@pytest.mark.parametrize(
    ("termination_date", "expected"),
    [
        (date(2025, 1, 31), date(2025, 4, 29)),
        (date(2024, 2, 29), date(2024, 5, 28)),
        (date(2025, 11, 30), date(2026, 2, 27)),
    ],
)
def test_generic_deadline_month_end(termination_date: date, expected: date) -> None:
    assert deadline_from(termination_date, add_months=3, minus_days=1) == expected


def test_days_until_deadline() -> None:
    assert (
        days_until_deadline(
            date(2026, 1, 1), add_months=3, minus_days=1, today=date(2026, 3, 20)
        )
        == 11
    )
