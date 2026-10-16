from datetime import date

import pytest
from pydantic import ValidationError

from inbound_voice.intake import (
    ConveyancingIntake,
    EmploymentIntake,
    days_until_unfair_dismissal_deadline,
    unfair_dismissal_deadline,
)


def test_valid_conveyancing_intake() -> None:
    intake = ConveyancingIntake.model_validate(
        {
            "caller_name": "A. Caller",
            "phone": "+44 7700 900123",
            "email": "caller@example.com",
            "transaction_type": "purchase",
            "property_address": "10 High Street",
            "postcode": "SW1A 1AA",
            "price": "£350,000",
            "tenure": "leasehold",
            "new_build": False,
            "mortgage": True,
            "lender": "Example Bank",
            "first_time_buyer": True,
            "chain_status": "No chain",
            "estate_agent": "Example Estate Agents",
            "target_exchange_date": "2026-12-01",
            "target_completion_date": "2026-12-15",
            "other_side_solicitor": "Example Legal",
        }
    )
    assert intake.transaction_type == "purchase"
    assert intake.target_exchange_date == date(2026, 12, 1)


def test_invalid_conveyancing_enums_and_extra_fields() -> None:
    with pytest.raises(ValidationError):
        ConveyancingIntake.model_validate(
            {"transaction_type": "transfer", "unknown": "not allowed"}
        )


def test_valid_employment_intake() -> None:
    intake = EmploymentIntake.model_validate(
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


def test_invalid_employment_fields() -> None:
    with pytest.raises(ValidationError):
        EmploymentIntake.model_validate(
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
def test_unfair_dismissal_deadline_month_end(termination_date: date, expected: date) -> None:
    assert unfair_dismissal_deadline(termination_date) == expected


def test_days_until_deadline() -> None:
    assert days_until_unfair_dismissal_deadline(date(2026, 1, 1), today=date(2026, 3, 20)) == 11
