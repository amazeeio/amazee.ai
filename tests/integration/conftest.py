"""Fixtures for the real-LiteLLM integration suite.

Inherits tests/conftest.py (db, client, admin fixtures) via pytest conftest
chaining. Nothing here patches LiteLLMService — the whole point is that the
backend talks to the real proxies defined in docker-compose.integration.yml.

Entities must be created through backend flows (make_team/make_key), never via
the unit fixtures' direct DB inserts, or the LiteLLM side never happens.
"""

import asyncio
import os
import time
from uuid import uuid4

import httpx
import pytest

from app.db.models import DBRegion
from app.services.litellm import LiteLLMService

# Fixed names — docker-compose.integration.yml sets AI_TRIAL_REGION and
# CATALOG_MANAGED_REGIONS from these exact strings.
INTEGRATION_REGION_A = "integration-a"
INTEGRATION_REGION_B = "integration-b"

LITELLM_A_URL = os.getenv("INTEGRATION_LITELLM_A_URL", "http://litellm:4000")
LITELLM_B_URL = os.getenv("INTEGRATION_LITELLM_B_URL", "http://litellm2:4000")
LITELLM_MASTER_KEY = "sk-1234"
_MASTER_AUTH = {"Authorization": f"Bearer {LITELLM_MASTER_KEY}"}

MOCK_MODEL = "mock-gpt"
# Deliberately large per-token costs so a single mock completion accrues
# clearly nonzero spend and small max_budget values are exceeded in 1-2 calls.
MOCK_INPUT_COST_PER_TOKEN = 0.001
MOCK_OUTPUT_COST_PER_TOKEN = 0.002


@pytest.fixture
def db(_schema):
    """Override the unit db fixture: truncate WITHOUT restarting identity.

    LiteLLM entities persist across tests within a run and are keyed by app
    DB ids (format_team_id(region, team.id)). With RESTART IDENTITY every
    test's team would be id 1 again and collide with LiteLLM state left by
    earlier tests (e.g. inherit a tiny team cap). Monotonic ids keep LiteLLM
    ids unique per test; `down -v` resets both sides between runs.
    """
    from tests.conftest import _ALL_TABLES, TestingSessionLocal, engine
    from sqlalchemy import text

    with engine.connect() as conn:
        conn.execute(text(f"TRUNCATE {_ALL_TABLES} CASCADE"))
        conn.commit()

    session = TestingSessionLocal()
    try:
        yield session
    finally:
        session.close()


def wait_for(predicate, timeout=30, interval=0.5, message="condition"):
    """Poll until predicate() is truthy. LiteLLM flushes spend asynchronously
    (proxy_batch_write_at), so spend/cap/reset assertions must never be
    one-shot."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = predicate()
        if result:
            return result
        time.sleep(interval)
    raise AssertionError(f"timed out after {timeout}s waiting for {message}")


def _mock_model_registered(base_url: str) -> bool:
    resp = httpx.get(f"{base_url}/model/info", headers=_MASTER_AUTH, timeout=30)
    resp.raise_for_status()
    return any(
        m.get("model_name") == MOCK_MODEL for m in resp.json().get("data", [])
    )


def ensure_mock_model(base_url: str) -> None:
    """Register the mock model if absent. Re-callable: model-sync tests can
    legitimately delete it (reconcile converges regions to the catalog)."""
    if _mock_model_registered(base_url):
        return
    resp = httpx.post(
        f"{base_url}/model/new",
        headers=_MASTER_AUTH,
        json={
            "model_name": MOCK_MODEL,
            "litellm_params": {
                "model": f"openai/{MOCK_MODEL}",
                "api_key": "fake-upstream-key",
                "mock_response": "Hello from the integration mock.",
                "input_cost_per_token": MOCK_INPUT_COST_PER_TOKEN,
                "output_cost_per_token": MOCK_OUTPUT_COST_PER_TOKEN,
            },
        },
        timeout=30,
    )
    resp.raise_for_status()


@pytest.fixture(scope="session", autouse=True)
def mock_model():
    """Mock model with real accounting on both proxies, once per run."""
    ensure_mock_model(LITELLM_A_URL)
    ensure_mock_model(LITELLM_B_URL)


def _make_region(db, name, url, dedicated):
    region = DBRegion(
        name=name,
        label=f"Integration {name}",
        description="Real-LiteLLM integration test region",
        # Key creation provisions a vector DB on the region's Postgres, so
        # this must be the reachable compose pgvector instance.
        postgres_host="postgres",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        # Direct insert bypasses the https-only schema validator, the
        # established pattern for test regions (see tests/conftest.py).
        litellm_api_url=url,
        litellm_api_key=LITELLM_MASTER_KEY,
        is_active=True,
        is_dedicated=dedicated,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


@pytest.fixture
def litellm_region(db):
    """Region A: shared, backed by the real `litellm` compose service."""
    return _make_region(db, INTEGRATION_REGION_A, LITELLM_A_URL, dedicated=False)


@pytest.fixture
def dedicated_region(db):
    """Region B: dedicated, backed by the real `litellm2` compose service."""
    return _make_region(db, INTEGRATION_REGION_B, LITELLM_B_URL, dedicated=True)


def _auth(token):
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_team(client, admin_token, litellm_region):
    """Create a team through the API so the real LiteLLM bootstrap runs.

    require_purchase_for_requests=False, or new keys are gated behind a zero
    budget. No model restriction: create_key sends models=["all-team-models"],
    so a restricted team would reject mock-gpt completions.
    """

    def _make(**overrides):
        suffix = uuid4().hex[:8]
        payload = {
            "name": f"int-team-{suffix}",
            "admin_email": f"int-{suffix}@example.com",
            "phone": "1234567890",
            "billing_address": "123 Integration St, Test City, 12345",
            "region_id": litellm_region.id,
            "require_purchase_for_requests": False,
            **overrides,
        }
        resp = client.post("/teams", json=payload, headers=_auth(admin_token))
        assert resp.status_code == 201, f"team creation failed: {resp.text}"
        return resp.json()

    return _make


@pytest.fixture
def make_key(client, admin_token):
    """Create a private AI key through the API (real /key/generate)."""

    def _make(team_id, region_id, **overrides):
        payload = {
            "region_id": region_id,
            "name": f"int-key-{uuid4().hex[:6]}",
            "team_id": team_id,
            **overrides,
        }
        resp = client.post(
            "/private-ai-keys", json=payload, headers=_auth(admin_token)
        )
        assert resp.status_code == 200, f"key creation failed: {resp.text}"
        return resp.json()

    return _make


async def wait_for_key_spend(litellm_url, token, timeout=30, interval=0.5):
    """Poll /key/info until spend is positive; return it. Async twin of
    wait_for for the common 'spend has flushed' condition."""
    service = LiteLLMService(litellm_url, LITELLM_MASTER_KEY)
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        info = await service.get_key_info(token)
        spend = info.get("info", info).get("spend") or 0
        if spend > 0:
            return spend
        await asyncio.sleep(interval)
    raise AssertionError(
        f"key accrued no spend within {timeout}s — mock_response accounting "
        "broken on this LiteLLM version, or the flush window regressed"
    )


def completion(litellm_url, token, model=MOCK_MODEL, timeout=30):
    """One mock completion through the real proxy with a generated key."""
    return httpx.post(
        f"{litellm_url}/chat/completions",
        headers=_auth(token),
        json={
            "model": model,
            "messages": [{"role": "user", "content": "integration test"}],
        },
        timeout=timeout,
    )
