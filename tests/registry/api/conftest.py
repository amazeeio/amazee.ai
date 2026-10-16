from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
)

RUN_FIELDS = {"id", "step", "started_at", "finished_at", "status", "stats", "error", "stale"}
MODEL_FIELDS = {
    "provider", "model_id", "mode", "max_input_tokens", "max_output_tokens",
    "input_cost_per_token", "output_cost_per_token", "prices", "eol_date",
    "override_eol", "blocked", "status", "family", "manufacturer", "source", "first_seen", "last_seen", "price_scopes",
    "proxies",
}
TODAY = date(2026, 1, 1)
CHECKED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
# The problem texts of rule 4, the enable check.
UNKNOWN = "support is unknown: the support check has no result for this proxy"
UNSUPPORTED = "the proxy's LiteLLM version does not support the model"
UNAVAILABLE = "the proxy's cloud region does not offer the model"
PROVISIONED = "the proxy's cloud region offers the model only with provisioned throughput"
NEEDS_PROFILE = "the proxy's cloud region offers the model only through a geo profile; use the us. prefix"
BLOCKED = "the model is blocked"


@pytest.fixture
def models(registry_db):
    openai = DBRegistryProvider(name="openai")
    bedrock = DBRegistryProvider(name="bedrock")
    registry_db.add_all([openai, bedrock])
    registry_db.flush()
    common = {"source": "litellm", "first_seen": TODAY, "last_seen": TODAY}
    gpt = DBRegistryModel(
        provider_id=openai.id,
        model_id="gpt-4o",
        mode="chat",
        input_cost_per_token=Decimal("0.0000025"),
        output_cost_per_token=Decimal("0.00001"),
        prices={"input_cost_per_token": 2.5e-06},
        **common,
    )
    registry_db.add_all([
        gpt,
        DBRegistryModel(provider_id=openai.id, model_id="gpt-old", status="removed", **common),
        DBRegistryModel(
            provider_id=bedrock.id, model_id="anthropic.claude-x-v1:0",
            family="claudex", manufacturer="anthropic", **common,
        ),
    ])
    registry_db.flush()
    # The geo row goes in first, so the response order comes from the query.
    registry_db.add_all([
        DBRegistryModelPrice(
            model_id=gpt.id, scope_kind="geo", scope="eu", prices={"input_cost_per_token": 2.75e-06},
            source="litellm", last_seen=TODAY,
        ),
        DBRegistryModelPrice(
            model_id=gpt.id, scope_kind="base", scope="", prices={"input_cost_per_token": 2.5e-06},
            source="litellm", last_seen=TODAY,
        ),
    ])
    registry_db.commit()


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def bare_region(registry_db):
    """A region with no registry_proxies row, so it is not a proxy of the registry."""
    region = DBRegion(name="local-ap1", litellm_api_url="http://ap", litellm_api_key="sk", is_active=True)
    registry_db.add(region)
    registry_db.commit()
    return region


def model(db, provider, model_id):
    return (
        db.query(DBRegistryModel)
        .join(DBRegistryProvider)
        .filter(DBRegistryProvider.name == provider, DBRegistryModel.model_id == model_id)
        .one()
    )


@pytest.fixture
def deployed(registry_db, models, proxy_region):
    """Two proxies with deployments and support rows; `gpt-old` has none."""
    eu = DBRegion(
        name="local-eu1", litellm_api_url="http://litellm-eu:4000", litellm_api_key="sk-test", is_active=True
    )
    registry_db.add(eu)
    registry_db.flush()
    gpt = model(registry_db, "openai", "gpt-4o")
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    registry_db.add_all([
        DBRegistryProxy(region_id=proxy_region.id, cloud_regions={"bedrock": "us-east-1"}),
        DBRegistryProxy(region_id=eu.id),
        DBRegistryModelRegion(
            model_id=gpt.id, region_id=proxy_region.id, model_name="gpt-4o",
            litellm_model="openai/gpt-4o", litellm_deployment_id="us-gpt", enabled=True,
        ),
        DBRegistryModelRegion(
            model_id=gpt.id, region_id=eu.id, model_name="gpt-4o",
            litellm_model="openai/gpt-4o", litellm_deployment_id="eu-gpt", enabled=False,
        ),
        DBRegistryModelRegion(
            model_id=claude.id, region_id=proxy_region.id, model_name="claude-x",
            litellm_model="bedrock/us.anthropic.claude-x-v1:0", litellm_deployment_id="us-claude",
            enabled=True,
        ),
        DBRegistryModelSupport(
            model_id=gpt.id, region_id=proxy_region.id, priced=True, supported=True,
            region_available=True, checked_at=CHECKED_AT,
        ),
        DBRegistryModelSupport(
            model_id=gpt.id, region_id=eu.id, priced=True, supported=False, checked_at=CHECKED_AT,
        ),
        DBRegistryModelSupport(
            model_id=claude.id, region_id=proxy_region.id, priced=True, supported=None, checked_at=CHECKED_AT,
        ),
    ])
    registry_db.commit()
    return {"us": proxy_region, "eu": eu}


def support_row(model_id=None, region_id=None, supported=True, region_available=True):
    return DBRegistryModelSupport(
        model_id=model_id, region_id=region_id, priced=True, supported=supported,
        region_available=region_available, checked_at=CHECKED_AT,
    )


def deployment_state(db):
    """Every deployment and group link, to show that a refused call or a dry run changed nothing."""
    db.expire_all()
    deployments = [
        (d.id, d.model_id, d.region_id, d.model_name, d.litellm_model, d.litellm_deployment_id, d.enabled,
         d.litellm_params, d.model_info, d.prices)
        for d in db.query(DBRegistryModelRegion).order_by(DBRegistryModelRegion.id)
    ]
    links = sorted((link.model_region_id, link.access_group_id) for link in db.query(DBRegistryModelRegionGroup))
    return deployments, links


@pytest.fixture
def writable(registry_db, deployed):
    """The `deployed` proxies, imported, with the access groups alpha and beta and support rows for gpt-old."""
    old = model(registry_db, "openai", "gpt-old")
    registry_db.add_all([
        DBRegistryAccessGroup(slug="alpha", label="Alpha"),
        DBRegistryAccessGroup(slug="beta", label="Beta"),
        support_row(old.id, deployed["us"].id),
        support_row(old.id, deployed["eu"].id),
    ])
    for proxy in registry_db.query(DBRegistryProxy):
        proxy.imported_at = CHECKED_AT
    registry_db.commit()
    return deployed
