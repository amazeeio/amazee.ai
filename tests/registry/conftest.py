import pytest
from sqlalchemy import text

import app.registry.models as registry_models  # noqa: F401  registers the tables before create_all
from app.db.models import Base, DBRegion

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
