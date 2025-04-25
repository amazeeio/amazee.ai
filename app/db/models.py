from sqlalchemy import Boolean, Column, ForeignKey, Integer, String, DateTime, JSON
from sqlalchemy.orm import relationship, declarative_base
from datetime import datetime, UTC
from sqlalchemy.sql import func

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
    created_at = Column(DateTime(timezone=True), default=func.now())

    private_ai_keys = relationship("DBPrivateAIKey", back_populates="region")

class DBAPIToken(Base):
    __tablename__ = "api_tokens"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String)
    token = Column(String, unique=True, index=True)
    created_at = Column(DateTime(timezone=True), default=func.now())
    last_used_at = Column(DateTime(timezone=True), nullable=True)
    user_id = Column(Integer, ForeignKey("users.id"))

    owner = relationship("DBUser", back_populates="api_tokens")

class DBUser(Base):
    __tablename__ = "users"

    id = Column(Integer, primary_key=True, index=True)
    email = Column(String, unique=True, index=True)
    hashed_password = Column(String)
    is_active = Column(Boolean, default=True)
    is_admin = Column(Boolean, default=False)
    created_at = Column(DateTime(timezone=True), default=func.now())
    role = Column(String, default="user")  # user, admin, key_creator, read_only
    team_id = Column(Integer, ForeignKey("teams.id", name="fk_user_team"))
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    team = relationship("DBTeam", back_populates="users")
    private_ai_keys = relationship("DBPrivateAIKey", back_populates="owner")
    api_tokens = relationship("DBAPIToken", back_populates="owner")
    audit_logs = relationship("DBAuditLog", back_populates="user")

class DBTeam(Base):
    __tablename__ = "teams"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String, index=True)
    admin_email = Column(String, unique=True, index=True)
    phone = Column(String, nullable=True)
    billing_address = Column(String, nullable=True)
    is_active = Column(Boolean, default=True)
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    users = relationship("DBUser", back_populates="team")
    private_ai_keys = relationship("DBPrivateAIKey", back_populates="team")

class DBPrivateAIKey(Base):
    __tablename__ = "ai_tokens"

    id = Column(Integer, primary_key=True, index=True)
    database_name = Column(String, unique=True, index=True, nullable=True)
    name = Column(String, nullable=True)  # User-friendly display name
    database_host = Column(String, nullable=True)
    database_username = Column(String)
    database_password = Column(String, nullable=True)
    litellm_token = Column(String, nullable=True)
    litellm_api_url = Column(String, nullable=True)
    owner_id = Column(Integer, ForeignKey("users.id"))
    region_id = Column(Integer, ForeignKey("regions.id"))
    created_at = Column(DateTime(timezone=True), default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())
    team_id = Column(Integer, ForeignKey("teams.id"))

    owner = relationship("DBUser", back_populates="private_ai_keys")
    region = relationship("DBRegion", back_populates="private_ai_keys")
    team = relationship("DBTeam", back_populates="private_ai_keys")

    def to_dict(self):
        return {
            "id": self.id,
            "name": self.name,
            "database_name": self.database_name,
            "database_host": self.database_host,
            "database_username": self.database_username,
            "database_password": self.database_password,
            "litellm_token": self.litellm_token,
            "litellm_api_url": self.litellm_api_url or "",
            "region": self.region.name if self.region else None,
            "owner_id": self.owner_id,
            "team_id": self.team_id,
            "created_at": self.created_at
        }

class DBAuditLog(Base):
    __tablename__ = "audit_logs"

    id = Column(Integer, primary_key=True, index=True)
    timestamp = Column(DateTime, nullable=False, default=lambda: datetime.now(UTC))
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True)
    event_type = Column(String, nullable=False)
    resource_type = Column(String, nullable=False)
    resource_id = Column(String, nullable=True)
    action = Column(String, nullable=False)
    details = Column(JSON, nullable=True)
    ip_address = Column(String, nullable=True)
    user_agent = Column(String, nullable=True)
    request_source = Column(String, nullable=True)  # Values: 'frontend', 'api', or None

    user = relationship("DBUser", back_populates="audit_logs")