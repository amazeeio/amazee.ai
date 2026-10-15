"""Request and response models for the registry API."""

import json
from datetime import date, datetime
from typing import Annotated, Literal

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
# The slug goes into LiteLLM model_info.access_groups and team model lists, so it keeps the old catalog's rule.
SLUG_PATTERN = r"^[a-z0-9](?:[a-z0-9-]*[a-z0-9])?$"


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


class _Strict(BaseModel):
    """A request body."""

    # An unknown field would otherwise be dropped, and the call would change nothing.
    model_config = ConfigDict(extra="forbid")

    @model_validator(mode="before")
    @classmethod
    def _readable_input(cls, data):
        # A 422 echoes the input, and JSON cannot hold Infinity or NaN, so the error would fail
        # with a 500. Each one becomes a one-item list of its name, which no field takes as valid.
        return json.loads(json.dumps(data), parse_constant=lambda name: [name])


class _Update(_Strict):
    @model_validator(mode="after")
    def _check_body(self):
        if not self.model_fields_set:
            raise ValueError("the body changes nothing")
        return self


class RegistryModelUpdate(_Update):
    """Only the fields in the body change. `null` clears `family` and
    `override_eol`; a `null` price removes the scope's manual price."""

    family: str | None = Field(None, min_length=1)
    override_eol: date | None = None
    # No `| None` on these two: an explicit null gets a 422.
    blocked: StrictBool = None
    # Scope key (`base`, `geo:us`, `region:us-east-1`) -> LiteLLM price fields, or null.
    prices: dict[Scope, Annotated[dict[Name, Amount], Field(min_length=1)] | None] = Field(None, min_length=1)

    @field_validator("override_eol", mode="before")
    @classmethod
    def _iso_date(cls, value):
        # Pydantic reads a number, or a number as text, as Unix time, so 0 would give 1970-01-01.
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("override_eol must be a date as text, or null")
        return date.fromisoformat(value)


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
    # Null for a row that the API added and that is not on the proxy yet.
    litellm_deployment_id: str | None
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


# A provider or a cloud region name in `registry_proxies.cloud_regions`.
CloudName = Annotated[str, StringConstraints(pattern=r"^[a-z0-9_-]{1,64}$")]


class RegistryProxyUpdate(_Strict):
    """Only the given providers change; a null region removes the provider."""

    cloud_regions: dict[CloudName, CloudName | None] = Field(min_length=1)


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


class RegistryAccessGroupModelResponse(BaseModel):
    proxy: str
    model: str
    # The deployments of the model on this proxy that carry the group.
    model_names: list[str]


class RegistryAccessGroupResponse(BaseModel):
    slug: str
    label: str | None
    description: str | None
    models: list[RegistryAccessGroupModelResponse]


class RegistryAccessGroupCreate(_Strict):
    slug: str = Field(min_length=1, max_length=64, pattern=SLUG_PATTERN)
    label: str = Field(min_length=1)
    description: str | None = None


class RegistryAccessGroupUpdate(_Update):
    """The slug never changes: LiteLLM and teams know the group only by it."""

    # No `| None`: an explicit null gets a 422. A null description clears it.
    label: str = Field(None, min_length=1)
    description: str | None = None


class RegistryAccessGroupModelRef(_Strict):
    proxy: str
    model: str


class RegistryAccessGroupModels(_Strict):
    """The full list of models that carry the group; an empty list removes it from all."""

    models: list[RegistryAccessGroupModelRef]


class RegistryAliasResponse(BaseModel):
    proxy: str
    alias: str
    # A `model_name` on the same proxy, because LiteLLM maps a name to a name.
    target: str
    hidden: bool


class RegistryAliasUpdate(_Strict):
    target: str = Field(min_length=1)
    # StrictBool with no `| None`: an explicit null gets a 422.
    hidden: StrictBool = False


class RegistryProblem(BaseModel):
    """One reason why the registry refuses a write."""

    # Frozen, so a refusal can drop a problem that two checks both found.
    model_config = ConfigDict(frozen=True)

    message: str
    # The proxy and the model key that the problem is about, when it is about one.
    proxy: str | None = None
    model: str | None = None
    # 409 for a conflict with the stored state, 422 for a request that can never pass. Not in the body.
    status: Literal[409, 422] = Field(422, exclude=True)


class RegistryRefusal(BaseModel):
    """The body of every 409 and 422 that a registry check gives. Body validation keeps FastAPI's shape."""

    # Every message in one line, for a client that reads only `detail`.
    detail: str
    problems: list[RegistryProblem]


# A LiteLLM credential name. The secret stays on the proxy. Real names hold spaces
# (`AWS Bedrock`), but not at the ends, where they are easy to miss.
Credential = Annotated[
    str, StringConstraints(pattern=r"^[A-Za-z0-9._-](?:[A-Za-z0-9._ -]{0,126}[A-Za-z0-9._-])?$")
]


class RegistryProxyModelUpdate(_Strict):
    """Creates the model's deployment on the proxy, or changes every deployment it has there.

    Not an `_Update`: an empty body is valid and creates a deployment with the defaults.
    """

    # No `| None` on these four: an explicit null gets a 422. A null credential removes it.
    model_name: str = Field(None, min_length=1)
    litellm_model: str = Field(None, min_length=1)
    credential: Credential | None = None
    groups: list[str] = None
    enabled: StrictBool = None


class RegistryRollout(_Strict):
    models: list[str] = Field(min_length=1)
    # Proxy name: the credential name for the deployments that the rollout creates there, or null.
    proxies: dict[str, Credential | None] = Field(min_length=1)
    groups: list[str] = Field(default_factory=list)


class RegistryRolloutEntry(BaseModel):
    proxy: str
    model: str
    action: Literal["created", "updated"]
    model_name: str
    litellm_model: str


class RegistryPlanStep(BaseModel):
    """One step that the sync would take on a proxy. A dry run only shows it."""

    proxy: str
    action: Literal["create_model", "update_model", "delete_model", "write_alias_map", "read_proxy"]
    model: str | None = None
    model_name: str | None = None
    # The LiteLLM deployment id.
    deployment_id: str | None = None
    # What the sync would send, for a create or the full alias map.
    payload: dict | None = None
    # Each changed field or alias as {"from": live, "to": wanted}.
    changes: dict | None = None
    problems: list[str] = []


class RegistryDryRun[T](BaseModel):
    # The body of the write, or null where the write answers 204.
    result: T
    steps: list[RegistryPlanStep]
