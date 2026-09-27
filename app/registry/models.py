from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    UniqueConstraint,
)
from sqlalchemy.sql import func

from app.db.models import Base

# `removed`: the model left LiteLLM's list. The row stays.
MODEL_STATUSES = ("active", "removed")
# Where a model row came from. Only rows from the LiteLLM list can be marked
# removed when they leave it; a model we only know from a proxy never was
# on the list, so its absence there says nothing.
MODEL_SOURCES = ("litellm", "proxy")


class DBRegistryProvider(Base):
    __tablename__ = "registry_providers"

    id = Column(Integer, primary_key=True)
    # The LiteLLM provider id, the prefix in `litellm_params.model`.
    name = Column(String, nullable=False, unique=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DBRegistryModel(Base):
    __tablename__ = "registry_models"
    __table_args__ = (UniqueConstraint("provider_id", "model_id"),)

    id = Column(Integer, primary_key=True)
    provider_id = Column(
        Integer, ForeignKey("registry_providers.id", ondelete="RESTRICT"), nullable=False
    )
    # The provider's own model id, without the provider prefix or a Bedrock geo prefix.
    model_id = Column(String, nullable=False)
    mode = Column(String, nullable=True)
    max_input_tokens = Column(Integer, nullable=True)
    max_output_tokens = Column(Integer, nullable=True)
    input_cost_per_token = Column(Numeric, nullable=True)
    output_cost_per_token = Column(Numeric, nullable=True)
    # Every LiteLLM price field of the model, under LiteLLM's own names:
    # per token, image, second, character, page and so on. Empty when unknown.
    prices = Column(JSON, nullable=False, default=dict)
    # Names of the `supports_*` flags that are true, without the prefix.
    supports = Column(JSON, nullable=False, default=list)
    eol_date = Column(Date, nullable=True)
    status = Column(String, nullable=False, default="active")
    source = Column(String, nullable=False)
    first_seen = Column(Date, nullable=False)
    last_seen = Column(Date, nullable=False)
    # Moves only when a list field or the status changes, not on the daily last_seen.
    updated_at = Column(DateTime(timezone=True), server_default=func.now())


class DBRegistryAccessGroup(Base):
    __tablename__ = "registry_access_groups"

    id = Column(Integer, primary_key=True)
    slug = Column(String, nullable=False, unique=True)
    label = Column(String, nullable=True)
    description = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DBRegistryProxy(Base):
    """What the registry knows about one region's LiteLLM proxy."""

    __tablename__ = "registry_proxies"

    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True)
    litellm_version = Column(String, nullable=True)
    # The cloud region the proxy calls, per provider: {"bedrock": "us-east-1"}.
    cloud_regions = Column(JSON, nullable=False, default=dict)
    # Set once by the import. After it, the registry owns what runs on the proxy.
    imported_at = Column(DateTime(timezone=True), nullable=True)


class DBRegistryModelRegion(Base):
    """One deployment of a model on a region's proxy."""

    __tablename__ = "registry_model_regions"
    # A proxy can serve one model under several names, so the LiteLLM
    # deployment is the unit, not the model.
    __table_args__ = (UniqueConstraint("region_id", "litellm_deployment_id"),)

    id = Column(Integer, primary_key=True)
    model_id = Column(
        Integer, ForeignKey("registry_models.id", ondelete="RESTRICT"), nullable=False, index=True
    )
    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    model_name = Column(String, nullable=False)
    # `litellm_params.model` as deployed, with the geo prefix.
    litellm_model = Column(String, nullable=False)
    litellm_deployment_id = Column(String, nullable=True)
    # The price set on this proxy. Empty when the proxy uses LiteLLM's own price.
    input_cost_per_token = Column(Numeric, nullable=True)
    output_cost_per_token = Column(Numeric, nullable=True)
    # Every price field set in the deployment's litellm_params.
    prices = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class DBRegistryModelRegionGroup(Base):
    __tablename__ = "registry_model_region_groups"

    model_region_id = Column(
        Integer, ForeignKey("registry_model_regions.id", ondelete="CASCADE"), primary_key=True
    )
    access_group_id = Column(
        Integer, ForeignKey("registry_access_groups.id", ondelete="RESTRICT"), primary_key=True
    )


class DBRegistryRun(Base):
    __tablename__ = "registry_runs"

    id = Column(Integer, primary_key=True)
    step = Column(String, nullable=False)
    started_at = Column(DateTime(timezone=True), server_default=func.now())
    finished_at = Column(DateTime(timezone=True), nullable=True)
    status = Column(String, nullable=False, default="running")
    stats = Column(JSON, nullable=True)
    error = Column(String, nullable=True)


class DBRegistryModelSupport(Base):
    """Whether a region's proxy can price a model, from its own price list."""

    __tablename__ = "registry_model_support"

    model_id = Column(Integer, ForeignKey("registry_models.id", ondelete="CASCADE"), primary_key=True)
    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True)
    priced = Column(Boolean, nullable=False)
    # Whether the release list of the proxy's LiteLLM version has the model.
    # LiteLLM's lists are our source of truth for support. Empty when the
    # version or its release list is unknown.
    supported = Column(Boolean, nullable=True)
    # Whether the provider offers the model in the proxy's cloud region. Empty
    # when no source covers the provider, the model or that region.
    region_available = Column(Boolean, nullable=True)
    checked_at = Column(DateTime(timezone=True), nullable=False)


class DBRegistryLitellmVersion(Base):
    """LiteLLM's model list as shipped with one release, fetched once."""

    __tablename__ = "registry_litellm_versions"

    version = Column(String, primary_key=True)
    fetched_at = Column(DateTime(timezone=True), nullable=False)
    model_count = Column(Integer, nullable=True)
    payload = Column(JSON, nullable=True)
    # Set when the release list could not be fetched; the next run tries again.
    error = Column(String, nullable=True)


PRICE_SCOPE_KINDS = ("base", "geo", "cloud_region")


class DBRegistryModelPrice(Base):
    """A model's price in one scope: base, a geo (`us`, `eu`) or one cloud region."""

    __tablename__ = "registry_model_prices"

    model_id = Column(Integer, ForeignKey("registry_models.id", ondelete="CASCADE"), primary_key=True)
    scope_kind = Column(String, primary_key=True)
    # Empty for `base`.
    scope = Column(String, primary_key=True, default="")
    prices = Column(JSON, nullable=False)
    source = Column(String, nullable=False)
    last_seen = Column(Date, nullable=False)


class DBRegistryCloudAvailability(Base):
    """A provider offers a model in a cloud region, according to one source.

    Keyed by provider name and model id as text: a source can list models
    the registry does not have yet.
    """

    __tablename__ = "registry_cloud_availability"

    provider = Column(String, primary_key=True)
    model_id = Column(String, primary_key=True)
    cloud_region = Column(String, primary_key=True)
    source = Column(String, primary_key=True)
    # How the model can be called there: Bedrock ON_DEMAND, INFERENCE_PROFILE, ...
    call_types = Column(JSON, nullable=False, default=list)
    last_seen = Column(Date, nullable=False)


class DBRegistryModelLifecycle(Base):
    """A model's lifecycle as one source states it."""

    __tablename__ = "registry_model_lifecycle"

    provider = Column(String, primary_key=True)
    model_id = Column(String, primary_key=True)
    source = Column(String, primary_key=True)
    # As the source says it, for example ACTIVE or LEGACY.
    status = Column(String, nullable=True)
    launched_at = Column(Date, nullable=True)
    legacy_at = Column(Date, nullable=True)
    extended_access_until = Column(Date, nullable=True)
    eol_date = Column(Date, nullable=True)
    last_seen = Column(Date, nullable=False)
