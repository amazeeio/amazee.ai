from pydantic import BaseModel, ConfigDict, EmailStr, AfterValidator, Field
from typing import Optional, List, ClassVar, Literal, Dict, Annotated, Any
from datetime import date, datetime
from sqlalchemy.orm import relationship
from enum import Enum
from urllib.parse import urlparse
import ipaddress


class BudgetType(str, Enum):
    PERIODIC = "periodic"
    POOL = "pool"


def lowercase_email(v: str) -> str:
    """Validator to lowercase email addresses."""
    if v is None:
        return v
    return v.lower()


# Custom type for case-insensitive emails
CaseInsensitiveEmailStr = Annotated[EmailStr, AfterValidator(lowercase_email)]


class Token(BaseModel):
    access_token: str
    token_type: str


class TokenData(BaseModel):
    email: Optional[CaseInsensitiveEmailStr] = None


class EmailValidation(BaseModel):
    email: CaseInsensitiveEmailStr


class LoginData(BaseModel):
    username: CaseInsensitiveEmailStr  # Using username to match OAuth2 form field
    password: str


class UserBase(BaseModel):
    email: CaseInsensitiveEmailStr


class UserCreate(UserBase):
    password: Optional[str] = None
    team_id: Optional[int] = None
    role: Optional[str] = None
    receive_marketing_updates: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)


class UserUpdate(BaseModel):
    email: Optional[CaseInsensitiveEmailStr] = None
    is_admin: Optional[bool] = None
    receive_marketing_updates: Optional[bool] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None


class AdminUserUpdate(BaseModel):
    """Fields an admin/team-admin may change via PUT /users/{id}.

    Deliberately excludes password fields: this route never applied them (it
    silently dropped current_password/new_password). extra='forbid' now rejects
    them with a 422 instead of pretending to succeed. Self-service password
    changes go through /auth/me.
    """

    model_config = ConfigDict(extra="forbid")

    email: Optional[CaseInsensitiveEmailStr] = None
    is_admin: Optional[bool] = None
    is_active: Optional[bool] = None
    receive_marketing_updates: Optional[bool] = None


class User(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    role: Optional[str] = None
    receive_marketing_updates: bool = False
    model_config = ConfigDict(from_attributes=True)
    audit_logs: ClassVar = relationship("AuditLog", back_populates="user")


class UserMarketingUpdatesByEmailUpdate(BaseModel):
    email: CaseInsensitiveEmailStr
    receive_marketing_updates: bool


class APITokenBase(BaseModel):
    name: str


class APITokenCreate(APITokenBase):
    user_id: Optional[int] = None


class APIToken(APITokenBase):
    id: int
    token: str
    created_at: datetime
    last_used_at: Optional[datetime] = None
    user_id: int
    model_config = ConfigDict(from_attributes=True)


class APITokenResponse(APITokenBase):
    id: int
    created_at: datetime
    last_used_at: Optional[datetime] = None
    user_id: int
    model_config = ConfigDict(from_attributes=True)


class ProductBase(BaseModel):
    name: str
    id: str  # This is the Stripe product ID, format should be prod_XXX
    user_count: Optional[int] = 1
    keys_per_user: Optional[int] = 1
    total_key_count: Optional[int] = 6
    service_key_count: Optional[int] = 5
    max_budget_per_key: Optional[float] = 20.0
    rpm_per_key: Optional[int] = 500
    vector_db_count: Optional[int] = 1
    vector_db_storage: Optional[int] = 50  # Not used yet, should be a number in GiB
    renewal_period_days: int = 30
    active: bool = True


class ProductCreate(ProductBase):
    pass


class ProductUpdate(BaseModel):
    name: Optional[str] = None
    user_count: Optional[int] = None
    keys_per_user: Optional[int] = None
    total_key_count: Optional[int] = None
    service_key_count: Optional[int] = None
    max_budget_per_key: Optional[float] = None
    rpm_per_key: Optional[int] = None
    vector_db_count: Optional[int] = None
    vector_db_storage: Optional[int] = None
    renewal_period_days: Optional[int] = None
    active: Optional[bool] = None
    model_config = ConfigDict(from_attributes=True)


class Product(ProductBase):
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


_BLOCKED_HOST_ALIASES = {
    "localhost",
    "ip6-localhost",
    "ip6-loopback",
    "broadcasthost",
}


def _host_is_blocked(host: str) -> bool:
    """Reject hosts that a region endpoint should never legitimately point at.

    RFC1918/private addresses are intentionally allowed — regions often live on
    the cluster's internal network. Loopback, link-local (incl. the cloud
    metadata address 169.254.169.254), multicast and unspecified are blocked.
    Non-IP hostnames can't be judged statically and are allowed (admin-only);
    operator-side egress policy should still block loopback names. The common
    OS loopback aliases below are blocked as cheap defense-in-depth.
    """
    if not host or host.lower() in _BLOCKED_HOST_ALIASES:
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    # Unwrap IPv4-mapped IPv6 (e.g. ::ffff:169.254.169.254): is_loopback /
    # is_link_local return False for the mapped form, so without this a mapped
    # metadata/loopback address would bypass the block.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return ip.is_loopback or ip.is_link_local or ip.is_multicast or ip.is_unspecified


def validate_region_api_url(value: str) -> str:
    # Require https: the region master LiteLLM key is sent in request headers on
    # every proxied call, so a plain-http URL would transmit it in cleartext.
    # Internal services that need it can terminate TLS at the ingress.
    # NOTE: existing http:// regions in the DB keep serving live traffic and
    # stay editable — update_region only validates this field when its value
    # changes. Find legacy rows with:
    #   SELECT id, name, litellm_api_url FROM regions WHERE litellm_api_url LIKE 'http://%';
    parsed = urlparse(value)
    if parsed.scheme != "https":
        raise ValueError("litellm_api_url must use the https scheme")
    if not parsed.hostname or _host_is_blocked(parsed.hostname):
        raise ValueError("litellm_api_url must not target an internal/link-local host")
    return value


def validate_region_host(value: str) -> str:
    if _host_is_blocked(value):
        raise ValueError("host must not be an internal/link-local address")
    return value


RegionApiUrl = Annotated[str, AfterValidator(validate_region_api_url)]
RegionDbHost = Annotated[str, AfterValidator(validate_region_host)]


class RegionBase(BaseModel):
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    postgres_host: RegionDbHost
    postgres_port: int = 5432
    postgres_admin_user: str
    postgres_admin_password: str
    litellm_api_url: RegionApiUrl
    litellm_api_key: str
    is_active: bool = True
    is_dedicated: bool = False


class RegionCreate(RegionBase):
    pass


class RegionUpdate(BaseModel):
    # postgres_host / litellm_api_url are deliberately plain str here: legacy
    # regions may hold values (e.g. http:// URLs) that predate validation, and
    # a full PUT must not 422 on unchanged fields. update_region validates
    # these only when the submitted value differs from the stored one.
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    postgres_host: str
    postgres_port: int
    postgres_admin_user: str
    postgres_admin_password: Optional[str] = None
    litellm_api_url: str
    litellm_api_key: Optional[str] = None
    is_active: bool
    is_dedicated: bool
    model_config = ConfigDict(from_attributes=True)


class RegionResponse(BaseModel):
    id: int
    name: str
    label: Optional[str] = None
    description: Optional[str] = None
    postgres_host: str
    litellm_api_url: str
    is_active: bool
    is_dedicated: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class RegionAdminResponse(RegionResponse):
    # Admin view: includes DB connection identity but never secrets
    # (postgres_admin_password / litellm_api_key stay server-side).
    postgres_port: int
    postgres_admin_user: str


class RegionSummaryResponse(BaseModel):
    id: int
    name: str
    label: Optional[str] = None
    is_active: bool
    is_dedicated: bool
    model_config = ConfigDict(from_attributes=True)


class Region(RegionBase):
    # Response model: override validated input types so stored legacy values
    # (e.g. http:// URLs) don't fail serialization on reads/updates.
    postgres_host: str
    litellm_api_url: str
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PublicModel(BaseModel):
    model_id: str
    display_name: str
    provider: str
    region: str
    type: str
    context_length: Optional[int] = None
    status: Optional[str] = None


class PublicModelPricing(BaseModel):
    input_cost_per_token: Optional[float] = None
    output_cost_per_token: Optional[float] = None
    input_cost_per_million_tokens: Optional[float] = None
    output_cost_per_million_tokens: Optional[float] = None
    cache_creation_input_cost_per_million_tokens: Optional[float] = None
    cache_creation_input_cost_above_1hr_per_million_tokens: Optional[float] = None
    cache_read_input_cost_per_million_tokens: Optional[float] = None


class PublicModelCapabilities(BaseModel):
    supports_vision: bool = False
    supports_function_calling: bool = False
    supports_reasoning: bool = False
    supports_prompt_caching: bool = False


class PublicModelManufacturer(BaseModel):
    name: str
    website: Optional[str] = None
    version: Optional[str] = None
    release_date: Optional[str] = None
    attribution: Optional[str] = None


class PublicModelSummary(BaseModel):
    model_id: str
    display_name: str
    aliases: List[str] = Field(default_factory=list)
    metadata_raw: Optional[Any] = None
    provider: str
    type: str
    context_length: Optional[int] = None
    max_output_tokens: Optional[int] = None
    description: str
    manufacturer: Optional[PublicModelManufacturer] = None
    capabilities: PublicModelCapabilities
    pricing: PublicModelPricing


class PublicRegionModels(BaseModel):
    region: str
    status: str
    models: List[PublicModelSummary]


class BedrockMissingModel(BaseModel):
    """A model that exists upstream at a hyperscaler but isn't deployed to one of our LiteLLM regions.

    Provider-agnostic: the same shape is reused for AWS Bedrock today and is
    intended to cover Google Vertex / Azure Foundry once those endpoints exist.
    """

    model_id: str = Field(
        ...,
        description=(
            "Upstream provider model identifier, e.g. "
            "'anthropic.claude-opus-4-1-20250805-v1:0' for Bedrock."
        ),
    )
    model_name: str
    provider_name: str

    model_config = ConfigDict(populate_by_name=True, protected_namespaces=())


class ProviderRegionMissingModels(BaseModel):
    """Per-region-group summary of models we haven't deployed yet.

    For AWS Bedrock the ``region_group`` is a market code (US/EU/AU) and
    ``upstream_region`` is the AWS region name (e.g. ``us-east-1``).  For other
    providers these will carry the equivalent provider-native concepts.
    """

    region_group: str = Field(
        ...,
        description=(
            "Provider-native region grouping. For AWS this is the market code "
            "('US' / 'EU' / 'AU')."
        ),
    )
    upstream_region: str = Field(
        ...,
        description=(
            "Provider-native region used to look up upstream availability "
            "(e.g. 'us-east-1' for Bedrock, 'us-central1' for Vertex)."
        ),
    )
    regions: List[str] = Field(
        default_factory=list,
        description=(
            "DBRegion names whose deployed models contributed to the "
            "'configured' set for this region group."
        ),
    )
    available_model_count: int
    configured_model_count: int
    missing_model_count: int
    missing_models: List[BedrockMissingModel]


class ProviderMissingModelsReport(BaseModel):
    """Full report returned by /models/missing/{provider}."""

    provider: str = Field(
        ...,
        description="Hyperscaler being inspected: 'aws', 'google', or 'azure'.",
    )
    generated_at: datetime
    models_url: str
    is_authenticated: bool = Field(
        ...,
        description=(
            "Whether the caller authenticated. Authenticated admins also "
            "include private regions in 'configured'."
        ),
    )
    region_groups: List[ProviderRegionMissingModels]


class PrivateAIKeyBase(BaseModel):
    id: int
    database_name: Optional[str] = None
    name: Optional[str] = None
    database_host: Optional[str] = None
    database_username: Optional[str] = (
        None  # This is the database username, not the user's email
    )
    database_password: Optional[str] = None
    litellm_token: Optional[str] = None
    litellm_api_url: Optional[str] = None
    region: Optional[str] = None
    region_label: Optional[str] = None
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class PrivateAIKeyCreate(BaseModel):
    region_id: int = Field(
        description="Target region ID where the key and backing resources are created."
    )
    name: str = Field(description="Human-readable key name.")
    key_alias: Optional[str] = None
    owner_id: Optional[int] = Field(
        default=None,
        description=(
            "User-owned key mode. Set this to bind the key to a specific user. "
            "If omitted together with team_id, owner_id defaults to the current user."
        ),
    )
    team_id: Optional[int] = Field(
        default=None,
        description=(
            "Team-owned key mode. Set this to create a shared team key. "
            "Mutually exclusive with owner_id."
        ),
    )


class VectorDBCreate(BaseModel):
    region_id: int
    name: str
    owner_id: Optional[int] = None
    team_id: Optional[int] = None


class VectorDB(BaseModel):
    id: int
    database_name: str
    database_host: str
    database_username: str
    database_password: str
    owner_id: Optional[int] = None
    team_id: Optional[int] = None
    region: str
    name: str
    model_config = ConfigDict(from_attributes=True)


class TeamPrivateAIKeyCreate(PrivateAIKeyCreate):
    team_id: int  # Override to make team_id required
    owner_id: Optional[int] = None  # Explicitly set to None for team keys


class PrivateAIKey(PrivateAIKeyBase):
    owner_id: Optional[int] = None
    team_id: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class PrivateAIKeyDetail(PrivateAIKey):
    spend: Optional[float] = None
    key_name: Optional[str] = None
    key_alias: Optional[str] = None
    soft_budget_cooldown: Optional[bool] = None
    models: Optional[List[str]] = None
    max_parallel_requests: Optional[int] = None
    tpm_limit: Optional[int] = None
    rpm_limit: Optional[int] = None
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[datetime] = None
    expires: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    metadata: Optional[dict] = None
    model_config = ConfigDict(from_attributes=True)


class TrialAccessResponse(BaseModel):
    key: PrivateAIKey
    user: User
    token: Token
    team_id: int
    team_name: str


class BudgetPeriodUpdate(BaseModel):
    budget_duration: str


class TokenDurationUpdate(BaseModel):
    """Schema for updating a token's duration"""

    duration: str  # e.g. "30d" for 30 days, "1y" for 1 year


class PrivateAIKeySpendBasic(BaseModel):
    spend: float
    expires: Optional[datetime] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class PrivateAIKeySpend(BaseModel):
    spend: float
    expires: datetime
    created_at: datetime
    updated_at: datetime
    max_budget: Optional[float] = Field(
        default=None,
        description=(
            "Key spend cap from Amazee AI DB (spend_caps) for this key. "
            "Returns null when no key-level cap is configured."
        ),
    )
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[datetime] = None
    period_start: Optional[datetime] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    model_config = ConfigDict(from_attributes=True)


class SpendKeyItem(BaseModel):
    key_id: Optional[int] = None
    key_name: Optional[str] = None
    owner_id: Optional[int] = None
    team_id: Optional[int] = None
    spend: float
    max_budget: Optional[float] = Field(
        default=None,
        description=(
            "Key spend cap from Amazee AI DB (spend_caps) for this key. "
            "Returns null when no key-level cap is configured."
        ),
    )
    cached_spend: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[datetime] = None
    period_start: Optional[datetime] = None


class TeamSpendResponse(BaseModel):
    region_id: int
    region_name: str
    team_id: int
    team_name: str
    total_spend: float = Field(
        description=(
            "Team spend from provider/API aggregation. May include provider-side "
            "projection/cumulative behavior."
        )
    )
    total_budget: float = Field(
        description=(
            "Effective team budget currently projected/enforced in provider/API totals."
        )
    )
    # Current-period values (may differ from total_* when provider counters are cumulative)
    period_spend: Optional[float] = Field(
        default=None,
        description="Current period spend (period-local view).",
    )
    period_budget: Optional[float] = Field(
        default=None,
        description=(
            "Current period budget. For subscription-managed POOL/PERIODIC teams, "
            "this reflects ledger semantics for the active period."
        ),
    )
    total_prompt_tokens: Optional[int] = None
    total_completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    budget_duration: Optional[str] = Field(
        default=None,
        description="Active team budget window duration (e.g. 31d, 365d, 1mo).",
    )
    budget_reset_at: Optional[datetime] = Field(
        default=None,
        description="End timestamp of the active team budget window.",
    )
    period_start: Optional[datetime] = Field(
        default=None,
        description="Start timestamp of the active team budget window.",
    )
    periodic_budget: Optional["PeriodicTeamBudgetView | float"] = Field(
        default=None,
        description=(
            "PERIODIC teams: structured PeriodicTeamBudgetView. "
            "POOL teams with active subscription: current cycle subscription amount (float). "
            "Otherwise null."
        ),
    )
    key_count: int
    keys: List[SpendKeyItem]


class PeriodicTeamBudgetView(BaseModel):
    purchased_budget_cents: int
    purchased_budget: float
    remaining_budget_cents: int
    remaining_budget: float
    configured_max_budget_cents: int
    configured_max_budget: float


class UserSpendResponse(BaseModel):
    region_id: int
    region_name: str
    user_id: int
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    total_spend: float
    total_prompt_tokens: Optional[int] = None
    total_completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    key_count: int
    keys: List[SpendKeyItem]


class KeyLastUsedResponse(BaseModel):
    region_id: int
    key_id: int
    last_used_at: Optional[datetime] = Field(
        default=None,
        description=(
            "Timestamp the key was last used, derived from LiteLLM spend logs. "
            "Null when the key has never been used."
        ),
    )
    model_config = ConfigDict(from_attributes=True)


class DailyActivityModelBreakdown(BaseModel):
    """Per-model slice of a day's usage, taken from LiteLLM's breakdown block.

    Only present on daily-activity rows when the request opts in with
    ``include_breakdown=true``. The metric fields mirror the flat row and, for
    a given day, sum to the row's aggregate totals.
    """

    model: str = Field(
        description="LiteLLM model name, e.g. 'bedrock/us.anthropic.claude-sonnet-4-6'.",
    )
    spend: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = 0
    cache_creation_input_tokens: int = 0
    request_count: int = 0
    # `model` is a normal field here; opt out of pydantic's protected `model_`
    # namespace so it doesn't warn/clash.
    model_config = ConfigDict(from_attributes=True, protected_namespaces=())


class KeyDailyActivityRow(BaseModel):
    date: date
    spend: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0
    total_tokens: int = 0
    cache_read_input_tokens: int = Field(
        default=0,
        description=(
            "Prompt tokens served from LiteLLM's prompt cache on this day "
            "(billed at the cheaper cache-read rate)."
        ),
    )
    cache_creation_input_tokens: int = Field(
        default=0,
        description=(
            "Prompt tokens written to LiteLLM's prompt cache on this day "
            "(billed at the cache-write rate)."
        ),
    )
    request_count: int = Field(
        default=0,
        description="Number of API requests made with this key on this day.",
    )
    breakdown: Optional[List[DailyActivityModelBreakdown]] = Field(
        default=None,
        description=(
            "Per-model usage for this day, ordered by descending spend. Only "
            "populated when the request sets include_breakdown=true; omitted "
            "otherwise."
        ),
    )
    model_config = ConfigDict(from_attributes=True)


class KeyDailyActivityResponse(BaseModel):
    region_id: int
    key_id: int
    start_date: date
    end_date: date
    activity: List[KeyDailyActivityRow] = Field(
        description=(
            "Per-day usage rows for the key, ordered ascending by date. "
            "Days with no usage are omitted."
        )
    )
    model_config = ConfigDict(from_attributes=True)


class UserDailyActivityResponse(BaseModel):
    region_id: int
    user_id: int
    start_date: date
    end_date: date
    activity: List[KeyDailyActivityRow] = Field(
        description=(
            "Per-day usage rows aggregated across all of the user's keys, "
            "ordered ascending by date. Days with no usage are omitted."
        )
    )
    model_config = ConfigDict(from_attributes=True)


class TeamDailyActivityResponse(BaseModel):
    region_id: int
    team_id: int
    start_date: date
    end_date: date
    activity: List[KeyDailyActivityRow] = Field(
        description=(
            "Per-day usage rows aggregated across all of the team's keys, "
            "ordered ascending by date. Days with no usage are omitted."
        )
    )
    model_config = ConfigDict(from_attributes=True)


class SpendBudgetUpdateRequest(BaseModel):
    max_budget: Optional[float] = Field(default=None, ge=0)


class SpendBudgetUpdateResponse(BaseModel):
    scope: Literal["team", "key", "team_member"]
    source_endpoint: str
    region_id: int
    region_name: str
    team_id: Optional[int] = None
    user_id: Optional[int] = None
    key_id: Optional[int] = None
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    note: Optional[str] = None


class TeamSpendHistoryKeyItem(BaseModel):
    key_id: Optional[int] = None
    owner_id: Optional[int] = None
    key_name_snapshot: Optional[str] = None
    spend: float
    max_budget: Optional[float] = None
    prompt_tokens: Optional[int] = None
    completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None


class TeamSpendHistoryPeriodItem(BaseModel):
    period_start: datetime
    period_end: datetime
    budget_type: str
    total_spend: float
    total_budget: Optional[float] = None
    total_prompt_tokens: Optional[int] = None
    total_completion_tokens: Optional[int] = None
    total_tokens: Optional[int] = None
    subscription_remaining_cents: Optional[int] = None
    topup_remaining_cents: Optional[int] = None
    desired_remaining_cents: Optional[int] = None
    source: str
    stripe_event_id: Optional[str] = None
    stripe_invoice_id: Optional[str] = None
    stripe_subscription_id: Optional[str] = None
    keys: List[TeamSpendHistoryKeyItem]


class TeamSpendHistoryResponse(BaseModel):
    region_id: int
    region_name: str
    team_id: int
    team_name: str
    periods: List[TeamSpendHistoryPeriodItem]
    periodic_transactions: List["TeamPeriodicTransactionItem"] = Field(
        default_factory=list
    )


class TeamPeriodicTransactionItem(BaseModel):
    id: int
    payment_type: str
    amount_cents: int
    currency: str
    stripe_payment_id: str
    payment_date: datetime
    status: str
    sync_status: str
    source: str


class LiteLLMToken(BaseModel):
    id: int
    litellm_token: str
    litellm_api_url: str
    owner_id: Optional[int] = None
    team_id: Optional[int] = None
    region: str
    name: str
    model_config = ConfigDict(from_attributes=True)


class AuditLog(BaseModel):
    id: int
    timestamp: datetime
    user_id: Optional[int]
    event_type: str
    resource_type: str
    resource_id: Optional[str]
    action: str
    details: Optional[dict]
    ip_address: Optional[str]
    user_agent: Optional[str]
    request_source: Optional[str]
    referer: Optional[str] = None
    origin: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class AuditLogResponse(BaseModel):
    id: int
    timestamp: datetime
    user_id: Optional[int]
    user_email: Optional[str]
    event_type: str
    resource_type: str
    resource_id: Optional[str]
    action: str
    details: Optional[dict]
    ip_address: Optional[str]
    user_agent: Optional[str]
    request_source: Optional[str]
    referer: Optional[str] = None
    origin: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


class PaginatedAuditLogResponse(BaseModel):
    items: List[AuditLogResponse]
    total: int
    model_config = ConfigDict(from_attributes=True)


class AuditLogMetadata(BaseModel):
    event_types: List[str]
    resource_types: List[str]
    status_codes: List[str]
    model_config = ConfigDict(from_attributes=True)


# Team schemas
class TeamBase(BaseModel):
    name: str
    admin_email: CaseInsensitiveEmailStr
    phone: Optional[str] = None
    billing_address: Optional[str] = None


class TeamCreate(TeamBase):
    force_user_keys: bool = False
    hide_public_regions: bool = False
    budget_type: BudgetType = BudgetType.PERIODIC
    require_purchase_for_requests: bool = True
    region_id: int


class TeamUpdate(BaseModel):
    name: Optional[str] = None
    admin_email: Optional[CaseInsensitiveEmailStr] = None
    phone: Optional[str] = None
    billing_address: Optional[str] = None
    is_active: Optional[bool] = None
    is_always_free: Optional[bool] = None
    # Defaults to None (not False) like the other admin-only fields: otherwise a
    # GET-then-PUT round-trip marks it "set", and update_team's admin_only_fields
    # guard would 403 non-admins on innocuous edits (name/phone).
    force_user_keys: Optional[bool] = None
    hide_public_regions: Optional[bool] = None
    budget_type: Optional[BudgetType] = None
    require_purchase_for_requests: Optional[bool] = None


class Team(TeamBase):
    id: int
    is_active: bool
    is_always_free: bool
    force_user_keys: Optional[bool] = False
    hide_public_regions: bool = False
    budget_type: BudgetType
    require_purchase_for_requests: bool
    last_pool_purchase: Optional[datetime] = None
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_payment: Optional[datetime] = None
    deleted_at: Optional[datetime] = None
    retention_warning_sent_at: Optional[datetime] = None
    region_id: Optional[int] = None
    products: List[Product] = []
    allowed_regions: List[RegionSummaryResponse] = []
    model_config = ConfigDict(from_attributes=True)


class TeamWithUsers(Team):
    users: List[User] = []
    model_config = ConfigDict(from_attributes=True)


class UserAdminRegionResponse(BaseModel):
    user_id: int
    region_id: int
    region: RegionSummaryResponse
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class TeamSummary(BaseModel):
    id: int
    name: str
    model_config = ConfigDict(from_attributes=True)


class TeamOperation(BaseModel):
    team_id: int


class TeamMergeRequest(BaseModel):
    source_team_id: int
    conflict_resolution_strategy: Literal["delete", "rename", "cancel"]
    rename_suffix: Optional[str] = None  # For rename strategy


class TeamMergeResponse(BaseModel):
    success: bool
    message: str
    conflicts_resolved: Optional[List[str]] = None
    keys_migrated: int
    users_migrated: int


class UserRoleUpdate(BaseModel):
    role: str
    model_config = ConfigDict(from_attributes=True)


class SignInData(BaseModel):
    username: CaseInsensitiveEmailStr
    verification_code: str


class CheckoutSessionCreate(BaseModel):
    price_lookup_token: str


class PricingTableSession(BaseModel):
    client_secret: str
    model_config = ConfigDict(from_attributes=True)


class PricingTableCreate(BaseModel):
    pricing_table_id: str
    table_type: Literal["standard", "always_free", "gpt"] = "standard"
    stripe_publishable_key: Optional[str] = (
        None  # Optional on create, defaults to system config
    )
    model_config = ConfigDict(from_attributes=True)


class PricingTableResponse(BaseModel):
    pricing_table_id: str
    stripe_publishable_key: str  # Always included in response
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PricingTablesResponse(BaseModel):
    tables: Dict[str, PricingTableResponse | None]
    model_config = ConfigDict(from_attributes=True)


class SubscriptionCreate(BaseModel):
    product_id: str  # Stripe product ID
    model_config = ConfigDict(from_attributes=True)


class SubscriptionResponse(BaseModel):
    subscription_id: str
    product_id: str
    team_id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PortalRequest(BaseModel):
    return_url: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)


# Sales Dashboard schemas
class SalesProduct(BaseModel):
    id: str
    name: str
    active: bool
    model_config = ConfigDict(from_attributes=True)


class SalesTeam(BaseModel):
    id: int
    name: str
    admin_email: str
    created_at: datetime
    last_payment: Optional[datetime] = None
    is_always_free: bool
    budget_type: BudgetType
    products: List[SalesProduct]
    regions: List[str]
    total_spend: float
    trial_status: str
    model_config = ConfigDict(from_attributes=True)


class SalesTeamsResponse(BaseModel):
    teams: List[SalesTeam]
    model_config = ConfigDict(from_attributes=True)


class TeamRegionBudget(BaseModel):
    team_id: int
    region_id: int
    region_name: str
    total_spend: float
    total_budget: float
    model_config = ConfigDict(from_attributes=True)


class TeamRegionModelAliasesUpdateRequest(BaseModel):
    model_aliases: Dict[str, str] = Field(default_factory=dict)


class TeamRegionModelAliasesResponse(BaseModel):
    region_id: int
    team_id: int
    model_aliases: Dict[str, str] = Field(default_factory=dict)
    model_config = ConfigDict(from_attributes=True)


class PoolPurchaseRequest(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str
    purchased_at: datetime
    stripe_payment_id: str


class PoolPurchaseResponse(BaseModel):
    id: int
    team_id: int
    region_id: int
    amount_cents: int
    currency: str
    purchased_at: datetime
    stripe_payment_id: str
    created_at: datetime
    new_total_budget_cents: int
    keys_updated: int
    model_config = ConfigDict(from_attributes=True)


class PeriodicTopupRequest(BaseModel):
    amount_cents: int = Field(gt=0)
    currency: str
    purchased_at: datetime
    stripe_payment_id: str


class PeriodicTopupResponse(BaseModel):
    id: int
    team_id: int
    region_id: int
    amount_cents: int
    currency: str
    purchased_at: datetime
    stripe_payment_id: str
    created_at: datetime
    new_total_budget_cents: int
    budget_type: BudgetType = BudgetType.PERIODIC
    model_config = ConfigDict(from_attributes=True)


class PoolPurchaseHistoryItem(BaseModel):
    id: int
    amount_cents: int
    currency: str
    purchased_at: datetime
    stripe_payment_id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PoolPurchaseHistoryResponse(BaseModel):
    team_id: int
    region_id: int
    purchases: List[PoolPurchaseHistoryItem]
    model_config = ConfigDict(from_attributes=True)


class PoolRegionPurchaseHistoryItem(BaseModel):
    id: int
    team_id: int
    amount_cents: int
    currency: str
    purchased_at: datetime
    stripe_payment_id: str
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)


class PoolRegionPurchaseHistoryResponse(BaseModel):
    region_id: int
    purchases: List[PoolRegionPurchaseHistoryItem]
    model_config = ConfigDict(from_attributes=True)


class PeriodicBudgetStatusResponse(BaseModel):
    team_id: int
    region_id: int
    subscription_remaining_cents: int
    topup_remaining_cents: int
    desired_remaining_cents: int
    subscription_period_end: Optional[datetime] = None
    nearest_topup_expiry: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class UserSpendRegion(BaseModel):
    region_id: int
    region_name: str
    spend: float
    status: str
    max_budget: Optional[float] = None


class UserSpendTeam(BaseModel):
    team_id: int
    team_name: str
    spend: float
    regions: List[UserSpendRegion]


class UserSpendByEmailResponse(BaseModel):
    email: str
    total_spend: float
    teams: List[UserSpendTeam]
    cached_at: datetime


class SubscriptionCycleRequest(BaseModel):
    transaction_id: str
    budget_cents: int
    team_id: int
    region_id: int


class SubscriptionDeactivateRequest(BaseModel):
    transaction_id: str
    team_id: int
    region_id: int
    reason: Optional[str] = None


class SubscriptionCycleResponse(BaseModel):
    status: str
    team_id: int
    payment_id: Optional[int] = None
    budget_dollars: Optional[float] = None
    idempotent: bool = False


class SubscriptionDeactivateResponse(BaseModel):
    status: str
    team_id: int
    payment_id: Optional[int] = None
    idempotent: bool = False


class AdminModelRegionResponse(BaseModel):
    region_id: int
    region_name: str
    is_active: bool
    sync_status: str
    sync_error: Optional[str] = None
    synced_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)


class AdminModelBase(BaseModel):
    model_id: str
    display_name: str
    provider: str
    type: str
    context_length: Optional[int] = None
    max_output_tokens: Optional[int] = None
    description: Optional[str] = None
    real_eol: Optional[datetime] = None
    override_eol: Optional[datetime] = None
    is_active_globally: bool = True
    litellm_params: Optional[dict] = None


class AdminModelCreate(AdminModelBase):
    pass


class AdminModelUpdate(BaseModel):
    display_name: Optional[str] = None
    provider: Optional[str] = None
    type: Optional[str] = None
    context_length: Optional[int] = None
    max_output_tokens: Optional[int] = None
    description: Optional[str] = None
    real_eol: Optional[datetime] = None
    override_eol: Optional[datetime] = None
    is_active_globally: Optional[bool] = None
    litellm_params: Optional[dict] = None


class AdminModelResponse(AdminModelBase):
    id: int
    created_at: datetime
    updated_at: datetime
    deleted_at: Optional[datetime] = None
    regions: List[AdminModelRegionResponse] = Field(default_factory=list)
    model_config = ConfigDict(from_attributes=True)


class AdminModelRegionToggleRequest(BaseModel):
    model_id: int
    region_id: int
    is_active: bool


class ImportableModelResponse(BaseModel):
    model_id: str
    display_name: str
    provider: str
    type: str
    context_length: Optional[int] = None
    max_output_tokens: Optional[int] = None
    description: Optional[str] = None
    litellm_params: Optional[dict] = None
    credential_keys: List[str] = Field(default_factory=list)


class AdminModelImport(AdminModelBase):
    region_id: int


