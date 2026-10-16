import calendar
from datetime import date, timedelta
from typing import Literal

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator

Funding = Literal["private", "legal_expenses_insurance", "legal_aid", "no_win_no_fee"]
ContactPreference = Literal["phone", "email"]
TransactionType = Literal["purchase", "sale", "both", "remortgage"]
Tenure = Literal["freehold", "leasehold", "shared_ownership"]
EmploymentIssue = Literal[
    "dismissal", "redundancy", "discrimination", "settlement_agreement", "grievance"
]


class IntakeBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    caller_name: str | None = None
    phone: str | None = None
    email: EmailStr | None = None
    funding: Funding | None = None
    contact_preference: ContactPreference | None = None
    other_parties: list[str] = Field(default_factory=list)

    @field_validator("caller_name", "phone")
    @classmethod
    def strip_optional_text(cls, value: str | None) -> str | None:
        return value.strip() if value else value


class ConveyancingIntake(IntakeBase):
    transaction_type: TransactionType | None = None
    property_address: str | None = None
    postcode: str | None = None
    price: str | None = None
    tenure: Tenure | None = None
    new_build: bool | None = None
    mortgage: bool | None = None
    lender: str | None = None
    first_time_buyer: bool | None = None
    chain_status: str | None = None
    estate_agent: str | None = None
    target_exchange_date: date | None = None
    target_completion_date: date | None = None
    other_side_solicitor: str | None = None


class EmploymentIntake(IntakeBase):
    issue_type: EmploymentIssue | None = None
    employer_name: str | None = None
    job_title: str | None = None
    length_of_service: str | None = None
    termination_date: date | None = None
    last_act_complained_of: date | None = None
    acas_early_conciliation_started: bool | None = None
    key_dates: dict[str, date] = Field(default_factory=dict)


PRESETS: dict[str, type[IntakeBase]] = {
    "conveyancing": ConveyancingIntake,
    "employment": EmploymentIntake,
}


def unfair_dismissal_deadline(termination_date: date) -> date:
    """Return three calendar months less one day after the effective termination date."""
    month_index = termination_date.month - 1 + 3
    year = termination_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(termination_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day) - timedelta(days=1)


def days_until_unfair_dismissal_deadline(termination_date: date, today: date | None = None) -> int:
    return (unfair_dismissal_deadline(termination_date) - (today or date.today())).days
