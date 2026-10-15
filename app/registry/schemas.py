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
