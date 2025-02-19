from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime, JSON
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
    name = Column(String)
    token = Column(String, unique=True, index=True)
    created_at = Column(DateTime, default=datetime.utcnow)
    last_used_at = Column(DateTime, nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"))

    owner = relationship("DBUser", back_populates="api_tokens")

class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime, default=datetime.utcnow)

    private_ai_keys = relationship("DBPrivateAIKey", back_populates="owner")
    api_tokens = relationship("DBAPIToken", back_populates="owner")
    audit_logs = relationship("DBAuditLog", back_populates="user")

class DBPrivateAIKey(Base):
    __tablename__ = "ai_tokens"

    id = Column(Integer, primary_key=True, index=True)
    database_name = Column(String, unique=True, index=True)
    host = Column(String)
    username = Column(String)
    password = Column(String)
    litellm_token = Column(String)
    litellm_api_url = Column(String)
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
            "litellm_api_url": self.litellm_api_url or "",
            "region": self.region.name if self.region else None,
            "owner_id": self.owner_id
        }

class DBAuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=datetime.utcnow)
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event_type = Column(String, nullable=False)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)

    user = relationship("DBUser", back_populates="audit_logs")