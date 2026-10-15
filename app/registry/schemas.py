"""Request and response models for the registry API."""

import json
from datetime import date, datetime
from typing import Annotated

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StrictBool,
    StringConstraints,
    field_validator,
    model_validator,
)

from app.registry.prices import SCOPE_KINDS

Scope = Annotated[str, StringConstraints(pattern=rf"^(base|({'|'.join(SCOPE_KINDS)}):[a-z0-9-]+)$")]
# Stricter than price_fields(), which takes any name that contains `cost`.
Name = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_]*cost[a-z0-9_]*$")]
Amount = Annotated[float, Field(ge=0, allow_inf_nan=False, strict=True)]


class RegistryRunResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    step: str
    started_at: datetime | None
    finished_at: datetime | None
    status: str
    stats: dict | None
    error: str | None
    # The ORM row has no such attribute, so the default lets model_validate read it.
    stale: bool = False


class RegistryModelPriceResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    scope_kind: str
    scope: str
    prices: dict
    source: str


class RegistryModelProxyResponse(BaseModel):
    proxy: str
    # Null when the proxy has no support row (priced to checked_at) or no deployment (enabled).
    priced: bool | None = None
    supported: bool | None = None
    region_available: bool | None = None
    checked_at: datetime | None = None
    enabled: bool | None = None


class RegistryModelResponse(BaseModel):
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
    override_eol: date | None
    blocked: bool
    status: str
    family: str | None
    manufacturer: str | None
    source: str
    first_seen: date
    last_seen: date
    price_scopes: list[RegistryModelPriceResponse]
    proxies: list[RegistryModelProxyResponse]


class RegistryModelUpdate(BaseModel):
    """Only the fields in the body change. `null` clears `family` and
    `override_eol`; a `null` price removes the scope's manual price."""

    # An unknown field would otherwise be dropped, and the call would change nothing.
    model_config = ConfigDict(extra="forbid")

    family: str | None = Field(None, min_length=1)
    override_eol: date | None = None
    # No `| None` on these two: an explicit null gets a 422.
    blocked: StrictBool = None
    # Scope key (`base`, `geo:us`, `region:us-east-1`) -> LiteLLM price fields, or null.
    prices: dict[Scope, Annotated[dict[Name, Amount], Field(min_length=1)] | None] = Field(None, min_length=1)

    @model_validator(mode="before")
    @classmethod
    def _readable_input(cls, data):
        # A 422 echoes the input, and JSON cannot hold Infinity or NaN, so the error would fail
        # with a 500. A one-item list keeps their name, and no field accepts a list.
        return json.loads(json.dumps(data), parse_constant=lambda name: [name])

    @field_validator("override_eol", mode="before")
    @classmethod
    def _date_as_text(cls, value):
        # Pydantic reads a number as Unix time, so 0 would give 1970-01-01.
        if value is not None and not isinstance(value, str):
            raise ValueError("override_eol must be a date as text, or null")
        return value

    @model_validator(mode="after")
    def _check_body(self):
        if not self.model_fields_set:
            raise ValueError("the body changes nothing")
        return self


class RegistryLifecycleResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    source: str
    status: str | None
    launched_at: date | None
    legacy_at: date | None
    extended_access_until: date | None
    eol_date: date | None
    last_seen: date


class RegistryAvailabilityResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    cloud_region: str
    source: str
    call_types: list
    last_seen: date


class RegistryHistoryResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    recorded_at: datetime
    state: dict


class RegistryDeploymentResponse(BaseModel):
    proxy: str
    model_name: str
    litellm_model: str
    enabled: bool
    input_cost_per_token: float | None
    output_cost_per_token: float | None
    prices: dict
    access_groups: list[str]
    # The name of the LiteLLM credential, or null when the credential is inline. Never its values.
    credential: str | None


class RegistryModelDeploymentResponse(RegistryDeploymentResponse):
    # The price the registry writes into this deployment.
    price: RegistryModelPriceResponse | None


class RegistryModelDetailResponse(RegistryModelResponse):
    supports: list[str]
    lifecycle: list[RegistryLifecycleResponse]
    availability: list[RegistryAvailabilityResponse]
    deployments: list[RegistryModelDeploymentResponse]
    history: list[RegistryHistoryResponse]


class RegistryProviderResponse(BaseModel):
    provider: str
    # The proxies with a deployment of one of the provider's models.
    proxies: list[str]
    models: int
    deployed_models: int


class RegistryProxyResponse(BaseModel):
    proxy: str
    region_id: int
    litellm_version: str | None
    cloud_regions: dict
    imported_at: datetime | None
    # The providers with a deployment on this proxy.
    providers: list[str]
    # Who may change what runs on the proxy: `catalog` or `none`.
    owner: str
    models_supported: int
    models_enabled: int


class RegistryProxyModelResponse(BaseModel):
    key: str
    status: str
    eol_date: date | None
    support: RegistryModelProxyResponse
    deployments: list[RegistryDeploymentResponse]


class RegistryProxyDetailResponse(RegistryProxyResponse):
    checked_at: datetime | None
    models: list[RegistryProxyModelResponse]


class RegistryEventResponse(BaseModel):
    key: str
    proxy: str | None
    type: str
    created_at: datetime
    data: dict
