from enum import Enum
from pydantic import BaseModel
from typing import Optional
from datetime import datetime

class LimitType(Enum):
    CONTROL_PLANE = "control_plane"
    DATA_PLANE = "data_plane"

class ResourceType(Enum):
    # Control Plane Type Resources
    USER_KEY = "user_key"        # Personal keys owned by users
    SERVICE_KEY = "service_key"  # Team-owned service keys
    USER = "user"
    VECTOR_DB = "vector_db"
    GPT_INSTANCE = "gpt_instance"

    # Data Plane Type Resources
    BUDGET = "max_budget"
    RPM = "rpm"
    STORAGE = "storage"
    DOCUMENT = "document"

class UnitType(Enum):
    COUNT = "count"
    DOLLAR = "dollar"
    GB = "gigabyte"

class OwnerType(Enum):
    SYSTEM = "system"
    TEAM = "team"
    USER = "user"

class LimitSource(Enum):
    PRODUCT = "product"
    DEFAULT = "default"
    MANUAL = "manual"

class LimitedResourceBase(BaseModel):
    owner_type: OwnerType
    owner_id: int
    resource: ResourceType
    limit_type: LimitType
    unit: UnitType
    max_value: float
    current_value: Optional[float] = None

class LimitedResourceCreate(LimitedResourceBase):
    limited_by: LimitSource
    set_by: Optional[str] = None
    model_config = {"from_attributes": True}

class LimitedResource(LimitedResourceBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    limited_by: LimitSource
    set_by: Optional[str] = None
    model_config = {"from_attributes": True}

class OverwriteLimitRequest(LimitedResourceBase):
    model_config = {"from_attributes": True}

class ResetLimitRequest(BaseModel):
    owner_type: OwnerType
    owner_id: int
    resource: ResourceType
    model_config = {"from_attributes": True}
