"""Response models for the registry API."""

from datetime import date, datetime

from pydantic import BaseModel, ConfigDict


class RegistryRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step: str
    started_at: datetime | None
    finished_at: datetime | None
    status: str
    stats: dict | None
    error: str | None


class RegistryModelPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope_kind: str
    scope: str
    prices: dict
    source: str


class RegistryModelResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    provider: str
    model_id: str
    mode: str | None
    max_input_tokens: int | None
    max_output_tokens: int | None
    # Float, so the Numeric columns go out as JSON numbers, not strings.
    input_cost_per_token: float | None
    output_cost_per_token: float | None
    prices: dict
    eol_date: date | None
    status: str
    source: str
    first_seen: date
    last_seen: date
    price_scopes: list[RegistryModelPriceResponse]
