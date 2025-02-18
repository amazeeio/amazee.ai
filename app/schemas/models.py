from pydantic import BaseModel, EmailStr, ConfigDict
from typing import Optional, List
from datetime import datetime

class Token(BaseModel):
    access_token: str
    token_type: str

class TokenData(BaseModel):
    email: Optional[str] = None

class UserBase(BaseModel):
    email: str
    is_admin: bool = False

class UserCreate(UserBase):
    password: str

class UserUpdate(BaseModel):
    email: Optional[str] = None
    is_admin: Optional[bool] = None

class User(UserBase):
    id: int
    is_active: bool
    model_config = ConfigDict(from_attributes=True)

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
    host: str
    username: str  # This is the database username, not the user's email
    password: str
    litellm_token: str
    litellm_api_url: str
    region: Optional[str] = None
    model_config = ConfigDict(from_attributes=True)

class PrivateAIKeyCreate(BaseModel):
    region_id: int

class PrivateAIKey(PrivateAIKeyBase):
    owner_id: int
    model_config = ConfigDict(from_attributes=True)