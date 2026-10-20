from datetime import UTC, date, datetime
from decimal import Decimal

import pytest

from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
)

RUN_FIELDS = {"id", "step", "started_at", "finished_at", "status", "stats", "error", "stale"}
MODEL_FIELDS = {
    "provider", "model_id", "mode", "max_input_tokens", "max_output_tokens",
    "input_cost_per_token", "output_cost_per_token", "prices", "eol_date",
    "status", "family", "manufacturer", "source", "first_seen", "last_seen", "price_scopes",
    "proxies",
}
TODAY = date(2026, 1, 1)
CHECKED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)


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
            enabled=True, litellm_params={"litellm_credential_name": "AWS Bedrock"},
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
