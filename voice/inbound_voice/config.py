from pathlib import Path
from typing import Any

import yaml
from pydantic import BaseModel, ConfigDict


class OfficeHours(BaseModel):
    model_config = ConfigDict(extra="forbid")

    days: list[str]
    opens: str
    closes: str
    timezone: str = "Europe/London"


class Fee(BaseModel):
    model_config = ConfigDict(extra="forbid")

    service: str
    price: str
    notes: str


class FirmConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    firm_name: str
    office_hours: OfficeHours
    practice_areas: list[str]
    fees: list[Fee]
    complaints_contact: str
    complaints_procedure: str
    legal_ombudsman_information: str
    disclaimer: str
    greeting: str


def load_firm_config(path: str | Path) -> FirmConfig:
    with Path(path).open(encoding="utf-8") as config_file:
        data: Any = yaml.safe_load(config_file)
    return FirmConfig.model_validate(data)
