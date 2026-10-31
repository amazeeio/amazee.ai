from collections import defaultdict

from sqlalchemy import (
    JSON,
    Boolean,
    Column,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
    false,
)
from sqlalchemy.sql import func

from app.db.models import Base




class DBRegistryProvider(Base):
    __tablename__ = "registry_providers"

    id = Column(Integer, primary_key=True)
    # The upstream service that answers the call. This is usually the prefix in
    # `litellm_params.model`, but Bedrock Mantle on the OpenAI route has the prefix `openai`.
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
    # Set by an admin. The EOL step copies it to eol_date before any source.
    override_eol = Column(Date, nullable=True)
    # A human override. No job changes it.
    blocked = Column(Boolean, nullable=False, default=False, server_default=false())
    # `active`; `deprecated` when the strongest lifecycle source says the provider
    # retires the model; `removed` once the model left LiteLLM's list, and
    # `removed` beats `deprecated`. The row stays.
    status = Column(String, nullable=False, default="active")
    # The same model at two providers has the same family. The daily step
    # fills it only when it is empty, so a manual value stays.
    family = Column(String, nullable=True, index=True)
    # The model maker from the id prefix, or from another model of the same
    # family. Empty when no source names it.
    manufacturer = Column(String, nullable=True)
    # `litellm` or `proxy`. Only rows from the list can be marked removed when
    # they leave it; a model only a proxy knows never was on the list.
    source = Column(String, nullable=False)
    first_seen = Column(Date, nullable=False)
    last_seen = Column(Date, nullable=False)
    # Moves only on a real change to the model or its prices, not on the daily last_seen.
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
    # Set once by the import. The API adds planned deployments to the proxy only after it.
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
    # Secret values removed; os.environ/ references and credential names kept.
    litellm_params = Column(JSON, nullable=False, default=dict)
    # As /model/info returns it, with LiteLLM's list data merged in that cannot be told apart.
    # Empty for a row that the API added.
    model_info = Column(JSON, nullable=False, default=dict)
    enabled = Column(Boolean, nullable=False, default=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    # On amazee.ai's clock. changed_at later than synced_at means a change waits for the sync.
    synced_at = Column(DateTime(timezone=True), nullable=True)
    changed_at = Column(DateTime(timezone=True), nullable=True)
    # The message of the last sync step that failed, scrubbed; empty after a success.
    sync_error = Column(String, nullable=True)


class DBRegistryModelRegionGroup(Base):
    __tablename__ = "registry_model_region_groups"

    model_region_id = Column(
        Integer, ForeignKey("registry_model_regions.id", ondelete="CASCADE"), primary_key=True
    )
    access_group_id = Column(
        Integer, ForeignKey("registry_access_groups.id", ondelete="RESTRICT"), primary_key=True
    )


class DBRegistryAlias(Base):
    """One entry of a proxy's `model_group_alias` map; `target` is a `model_name` on that proxy, not a model key."""

    __tablename__ = "registry_aliases"
    __table_args__ = (UniqueConstraint("region_id", "alias"),)

    id = Column(Integer, primary_key=True)
    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    alias = Column(String, nullable=False)
    target = Column(String, nullable=False)
    hidden = Column(Boolean, nullable=False, default=False, server_default=false())
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DBRegistryCredential(Base):
    """A named LiteLLM credential. The values never leave the registry in a response."""

    __tablename__ = "registry_credentials"

    id = Column(Integer, primary_key=True)
    name = Column(String, nullable=False, unique=True)
    # Empty for a credential that the adopt import read from a proxy.
    provider = Column(String, nullable=True)
    # Fernet token of the values JSON. Empty when the registry has no values,
    # and then the sync never pushes the credential.
    values_encrypted = Column(Text, nullable=True)
    credential_info = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now())


class DBRegistryCredentialRegion(Base):
    __tablename__ = "registry_credential_regions"

    credential_id = Column(
        Integer, ForeignKey("registry_credentials.id", ondelete="CASCADE"), primary_key=True
    )
    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), primary_key=True)


class DBRegistrySyncRun(Base):
    """One push of registry state to one or more proxies."""

    __tablename__ = "registry_sync_runs"

    id = Column(Integer, primary_key=True)
    # write, retry, repair or daily.
    trigger = Column(String, nullable=False)
    # pending, running, ok or failed.
    status = Column(String, nullable=False, default="pending")
    region_ids = Column(JSON, nullable=False, default=list)
    # What to push per region id. The run reads the wanted state again from
    # the database when it starts, so the scope holds ids, not payloads.
    scope = Column(JSON, nullable=False, default=dict)
    stats = Column(JSON, nullable=True)
    error = Column(String, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    started_at = Column(DateTime(timezone=True), nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


class DBRegistrySyncStep(Base):
    __tablename__ = "registry_sync_steps"

    id = Column(Integer, primary_key=True)
    run_id = Column(
        Integer, ForeignKey("registry_sync_runs.id", ondelete="CASCADE"), nullable=False, index=True
    )
    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=False)
    position = Column(Integer, nullable=False)
    action = Column(String, nullable=False)
    model = Column(String, nullable=True)
    model_name = Column(String, nullable=True)
    deployment_id = Column(String, nullable=True)
    model_region_id = Column(
        Integer, ForeignKey("registry_model_regions.id", ondelete="SET NULL"), nullable=True
    )
    credential = Column(String, nullable=True)
    payload = Column(JSON, nullable=True)
    changes = Column(JSON, nullable=True)
    # pending, ok, failed or skipped.
    status = Column(String, nullable=False, default="pending")
    message = Column(String, nullable=True)
    finished_at = Column(DateTime(timezone=True), nullable=True)


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
    payload = Column(JSON, nullable=True)
    # Set when the release list could not be fetched; the next run tries again.
    error = Column(String, nullable=True)


class DBRegistryModelPrice(Base):
    """A model's price in one scope: base, a geo (`us`, `eu`) or one cloud region."""

    __tablename__ = "registry_model_prices"

    model_id = Column(Integer, ForeignKey("registry_models.id", ondelete="CASCADE"), primary_key=True)
    # `base`, `geo` or `cloud_region`.
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


class DBRegistryModelHistory(Base):
    """One model's tracked state, with a new row only when the state changes."""

    __tablename__ = "registry_model_history"
    # The history step reads the newest row of each model through this index.
    __table_args__ = (Index("ix_registry_model_history_model_id_recorded_at", "model_id", "recorded_at"),)

    id = Column(Integer, primary_key=True)
    model_id = Column(Integer, ForeignKey("registry_models.id", ondelete="CASCADE"), nullable=False)
    recorded_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False)
    # Status, EOL date, headline prices and support flags per region. Region ids are
    # text and prices are floats, as JSON gives them back, so only real changes differ.
    state = Column(JSON, nullable=False)


class DBRegistryEvent(Base):
    """One change to a model that the daily history step found."""

    __tablename__ = "registry_events"

    id = Column(Integer, primary_key=True)
    # model.discovered, model.eol_announced, model.eol_changed, model.deprecated or model.support_changed.
    type = Column(String, nullable=False, index=True)
    model_id = Column(Integer, ForeignKey("registry_models.id", ondelete="CASCADE"), nullable=False, index=True)
    # Set only for a support change, which is about one proxy.
    region_id = Column(Integer, ForeignKey("regions.id", ondelete="CASCADE"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now(), nullable=False, index=True)
    data = Column(JSON, nullable=False)


def models_by_ident(db) -> dict[tuple[str, str], DBRegistryModel]:
    """Every registry model by (provider name, model_id)."""
    return {
        (provider, model.model_id): model
        for model, provider in db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
    }


def prices_by_model(db) -> dict[int, list[DBRegistryModelPrice]]:
    """Every price row, grouped by model id."""
    grouped: dict[int, list[DBRegistryModelPrice]] = defaultdict(list)
    for price in db.query(DBRegistryModelPrice):
        grouped[price.model_id].append(price)
    return grouped
