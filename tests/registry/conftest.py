from datetime import date

import pytest
from sqlalchemy import text

# Import the tables here, so _REGISTRY_TABLES does not depend on app.main importing the registry.
import app.registry.models  # noqa: F401
from app.db.models import Base, DBRegion
from app.registry.models import DBRegistryModel, DBRegistryProvider

_REGISTRY_TABLES = ", ".join(
    f'"{t.name}"' for t in Base.metadata.sorted_tables if t.name.startswith("registry_")
)


@pytest.fixture
def registry_db(db):
    # The shared db fixture clears these tables only because app.main imports
    # the registry. Clear them here too, so these tests do not depend on that.
    db.execute(text(f"TRUNCATE {_REGISTRY_TABLES} RESTART IDENTITY CASCADE"))
    db.commit()
    return db


@pytest.fixture
def proxy_region(registry_db):
    region = DBRegion(
        name="local-us1",
        litellm_api_url="http://litellm:4000",
        litellm_api_key="sk-test",
        is_active=True,
    )
    registry_db.add(region)
    registry_db.commit()
    return region


def add_model(db, provider_name="bedrock", model_id="anthropic.claude-x-v1:0"):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, supports=[], status="active", source="litellm",
        first_seen=date(2026, 9, 1), last_seen=date(2026, 9, 1),
    )
    db.add(row)
    db.flush()
    return row
