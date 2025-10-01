from enum import Enum
from pydantic import BaseModel
from typing import Optional, List
from datetime import datetime


class LimitType(Enum):
    CONTROL_PLANE = "control_plane"
    DATA_PLANE = "data_plane"


class ResourceType(Enum):
    # CP Type Resources
    KEY = "ai_key"
    USER = "user"
    VECTOR_DB = "vector_db"
    GPT_INSTANCE = "gpt_instance"

    # DP Type Resources
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


# Pydantic schemas using the Enums
class LimitedResourceBase(BaseModel):
    limit_type: LimitType
    resource: ResourceType
    unit: UnitType
    max_value: float
    current_value: Optional[float] = None
    owner_type: OwnerType
    owner_id: int
    limited_by: LimitSource
    set_by: Optional[str] = None


class LimitedResourceCreate(LimitedResourceBase):
    model_config = {"from_attributes": True}


class LimitedResource(LimitedResourceBase):
    id: int
    created_at: datetime
    updated_at: Optional[datetime] = None
    model_config = {"from_attributes": True}




class OverwriteLimitRequest(BaseModel):
    owner_type: OwnerType
    owner_id: int
    resource_type: ResourceType
    limit_type: LimitType
    unit: UnitType
    max_value: float
    current_value: Optional[float] = None
    model_config = {"from_attributes": True}


class ResetLimitRequest(BaseModel):
    owner_type: OwnerType
    owner_id: int
    resource_type: ResourceType
    model_config = {"from_attributes": True}
