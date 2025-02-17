from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime

Base = declarative_base()

class DBRegion(Base):
    __tablename__ = "regions"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, unique=True, index=True)
    postgres_host = Column(String)
    postgres_port = Column(Integer, default=5432)
    postgres_admin_user = Column(String)
    postgres_admin_password = Column(String)
    litellm_api_url = Column(String)
    litellm_api_key = Column(String)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime, default=datetime.utcnow)

    private_ai_keys = relationship("DBPrivateAIKey", back_populates="region")

class DBAPIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    token = Column(String, unique=True, index=True)
    name = Column(String)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"))

    user = relationship("DBUser", back_populates="api_tokens")

class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)

    private_ai_keys = relationship("DBPrivateAIKey", back_populates="owner")
    api_tokens = relationship("DBAPIToken", back_populates="user", cascade="all, delete-orphan")

class DBPrivateAIKey(Base):
    __tablename__ = "ai_tokens"

    id = Column(Integer, primary_key=True, index=True)
    database_name = Column(String, unique=True, index=True)
    host = Column(String)
    username = Column(String)
    password = Column(String)
    litellm_token = Column(String)
    owner_id = Column(Integer, ForeignKey("users.id"))
    region_id = Column(Integer, ForeignKey("regions.id"))

    owner = relationship("DBUser", back_populates="private_ai_keys")
    region = relationship("DBRegion", back_populates="private_ai_keys")

    def to_dict(self):
        return {
            "database_name": self.database_name,
            "host": self.host,
            "username": self.username,
            "password": self.password,
            "litellm_token": self.litellm_token,
            "region": self.region.name if self.region else None,
            "owner_id": self.owner_id
        }