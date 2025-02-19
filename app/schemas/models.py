from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, List, ClassVar
from datetime import datetime
from sqlalchemy import Column, Integer, DateTime, String, JSON, ForeignKey
from sqlalchemy.orm import relationship
from app.db.database import Base

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserBase(BaseModel):
    email: str

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    email: Optional[str] = None
    is_admin: Optional[bool] = None

class User(UserBase):
    id: int
    is_active: bool
    is_admin: bool
    model_config = ConfigDict(from_attributes=True)
    audit_logs: ClassVar = relationship("AuditLog", back_populates="user")

class APITokenBase(BaseModel):
    name: str

class APITokenCreate(APITokenBase):
    pass

class APIToken(APITokenBase):
    id: int
    token: str
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

class Region(RegionBase):
    id: int
    created_at: datetime
    model_config = ConfigDict(from_attributes=True)

class PrivateAIKeyBase(BaseModel):
    database_name: str
    name: Optional[str] = None
    database_host: str
    database_username: str  # This is the database username, not the user's email
    database_password: str
    litellm_token: str
    litellm_api_url: str
    region: Optional[str] = None
    created_at: Optional[datetime] = None
    model_config = ConfigDict(from_attributes=True)

class PrivateAIKeyCreate(BaseModel):
    region_id: int
    name: str

class PrivateAIKey(PrivateAIKeyBase):
    owner_id: int
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