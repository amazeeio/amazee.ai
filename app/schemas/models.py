from pydantic import BaseModel, ConfigDict, EmailStr
from typing import Optional, List, ClassVar, Literal
from datetime import datetime
from sqlalchemy.orm import relationship

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[EmailStr] = None

class EmailValidation(BaseModel):
    email: EmailStr

class LoginData(BaseModel):
    username: EmailStr  # Using username to match OAuth2 form field
    password: str

class UserBase(BaseModel):
    email: EmailStr

class UserCreate(UserBase):
    password: Optional[str] = None
    team_id: Optional[int] = None
    role: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class UserUpdate(BaseModel):
    email: Optional[EmailStr] = None
    is_admin: Optional[bool] = None
    current_password: Optional[str] = None
    new_password: Optional[str] = None

class User(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    team_id: Optional[int] = None
    team_name: Optional[str] = None
    role: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)
    audit_logs: ClassVar = relationship("AuditLog", back_populates="user")

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

class RegionBase(BaseModel):
    name: str
    postgres_host: str
    postgres_port: int = 5432
    postgres_admin_user: str
    postgres_admin_password: str
    litellm_api_url: str
    litellm_api_key: str
    is_active: bool = True

class RegionCreate(RegionBase):
    pass

class RegionUpdate(BaseModel):
    name: str
    postgres_host: str
    postgres_port: int
    postgres_admin_user: str
    postgres_admin_password: Optional[str] = None
    litellm_api_url: str
    litellm_api_key: Optional[str] = None
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

class RegionResponse(BaseModel):
    id: int
    name: str
    postgres_host: str
    litellm_api_url: str
    is_active: bool
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class Region(RegionBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class PrivateAIKeyBase(BaseModel):
    id: int
    database_name: Optional[str] = None
    name: Optional[str] = None
    database_host: Optional[str] = None
    database_username: Optional[str] = None  # This is the database username, not the user's email
    database_password: Optional[str] = None
    litellm_token: Optional[str] = None
    litellm_api_url: Optional[str] = None
    region: Optional[str] = None
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class PrivateAIKeyCreate(BaseModel):
    region_id: int
    name: str
    owner_id: Optional[int] = None
    team_id: Optional[int] = None

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

class BudgetPeriodUpdate(BaseModel):
    budget_duration: str

class TokenDurationUpdate(BaseModel):
    """Schema for updating a token's duration"""
    duration: str  # e.g. "30d" for 30 days, "1y" for 1 year

class PrivateAIKeySpend(BaseModel):
    spend: float
    expires: datetime
    created_at: datetime
    updated_at: datetime
    max_budget: Optional[float] = None
    budget_duration: Optional[str] = None
    budget_reset_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

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
    admin_email: EmailStr
    phone: Optional[str] = None
    billing_address: Optional[str] = None

class TeamCreate(TeamBase):
    pass

class TeamUpdate(BaseModel):
    name: Optional[str] = None
    admin_email: Optional[EmailStr] = None
    phone: Optional[str] = None
    billing_address: Optional[str] = None
    is_active: Optional[bool] = None
    is_always_free: Optional[bool] = None

class Team(TeamBase):
    id: int
    is_active: bool
    is_always_free: bool
    created_at: datetime
    updated_at: Optional[datetime] = None
    last_payment: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class TeamWithUsers(Team):
    users: List[User] = []
    model_config = ConfigDict(from_attributes=True)

class TeamOperation(BaseModel):
    team_id: int

class UserRoleUpdate(BaseModel):
    role: str
    model_config = ConfigDict(from_attributes=True)

class SignInData(BaseModel):
    username: EmailStr
    verification_code: str

class CheckoutSessionCreate(BaseModel):
    price_lookup_token: str

class ProductBase(BaseModel):
    name: str
    id: str # This is the Stripe product ID, format should be prod_XXX
    user_count: Optional[int] = 1
    keys_per_user: Optional[int] = 1
    total_key_count: Optional[int] = 6
    service_key_count: Optional[int] = 5
    max_budget_per_key: Optional[float] = 20.0
    rpm_per_key: Optional[int] = 500
    vector_db_count: Optional[int] = 1
    vector_db_storage: Optional[int] = 50 # Not used yet, should be a number in GiB
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

class PricingTableSession(BaseModel):
    client_secret: str
    model_config = ConfigDict(from_attributes=True)

class PricingTableCreate(BaseModel):
    pricing_table_id: str
    table_type: Literal["standard", "always_free"] = "standard"
    model_config = ConfigDict(from_attributes=True)

class PricingTableResponse(BaseModel):
    pricing_table_id: str
    updated_at: datetime
    model_config = ConfigDict(from_attributes=True)

class PricingTablesResponse(BaseModel):
    standard: PricingTableResponse | None
    always_free: PricingTableResponse | None
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