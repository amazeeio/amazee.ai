"""Tests for the /public/models/missing Bedrock-availability endpoint."""

from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch

import httpx

from app.api import public as public_api
from app.core.security import get_password_hash
from app.db.models import DBRegion, DBUser


def _clear_bedrock_catalog_cache():
    public_api._bedrock_catalog_cache["url"] = None
    public_api._bedrock_catalog_cache["data"] = None
    public_api._bedrock_catalog_cache["expires_at"] = datetime.min.replace(tzinfo=UTC)


def _make_public_region(db, name, suffix="public"):
    region = DBRegion(
        name=name,
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url=f"https://{suffix}.example",
        litellm_api_key=f"{suffix}-key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_dedicated_region(db, name, suffix="dedicated"):
    region = DBRegion(
        name=name,
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url=f"https://{suffix}.example",
        litellm_api_key=f"{suffix}-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_admin_user(db, email="missing-admin@example.com"):
    password = "AdminPassword123"
    user = DBUser(
        email=email,
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=True,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


def _get_token(client, email, password):
    resp = client.post("/auth/login", data={"username": email, "password": password})
    return resp.json()["access_token"]


def _upstream_catalog():
    """Three models in US, two in EU, one in AU; one INACTIVE that must be ignored."""
    return [
        {
            "modelId": "anthropic.claude-opus-4-7",
            "modelName": "Claude Opus 4.7",
            "providerName": "Anthropic",
            "regions": ["us-east-1", "eu-central-1", "ap-southeast-2"],
            "modelLifecycle": {"status": "ACTIVE"},
        },
        {
            "modelId": "amazon.nova-pro-v1:0",
            "modelName": "Nova Pro",
            "providerName": "Amazon",
            "regions": ["us-east-1", "eu-central-1"],
        },
        {
            "modelId": "meta.llama3-1-70b-instruct-v1:0",
            "modelName": "Llama 3.1 70B",
            "providerName": "Meta",
            "regions": ["us-east-1"],
        },
        {
            "modelId": "legacy.dont-show-me",
            "modelName": "Legacy",
            "providerName": "Legacy",
            "regions": ["us-east-1"],
            "modelLifecycle": {"status": "LEGACY"},
        },
    ]


def _model_info_with_bedrock(provider_ids):
    """Build a fake LiteLLMService.get_model_info() payload."""
    return {
        "data": [
            {
                "model_name": pid.split("/", 1)[-1],
                "litellm_params": {"model": pid},
                "model_info": {"litellm_provider": "bedrock"},
            }
            for pid in provider_ids
        ]
    }


def _patch_catalog_fetch(catalog):
    """Patch httpx.AsyncClient.get to return the given upstream catalog."""

    class _FakeResponse:
        def __init__(self, payload):
            self._payload = payload

        def raise_for_status(self):
            return None

        def json(self):
            return self._payload

    return patch.object(
        httpx.AsyncClient,
        "get",
        new=AsyncMock(return_value=_FakeResponse(catalog)),
    )


def test_missing_models_anonymous_reports_gaps_per_market(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")

    deployed = _model_info_with_bedrock(
        [
            # US has only Llama deployed; missing Opus + Nova.
            "bedrock/us.meta.llama3-1-70b-instruct-v1:0",
        ]
    )

    with patch("app.api.public.LiteLLMService") as mock_service_cls, _patch_catalog_fetch(
        _upstream_catalog()
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(return_value=deployed)
        response = client.get("/public/models/missing")

    assert response.status_code == 200
    body = response.json()
    assert body["is_authenticated"] is False
    assert body["models_url"].startswith("http")

    by_market = {m["market"]: m for m in body["markets"]}
    assert set(by_market) == {"US", "EU", "AU"}

    us = by_market["US"]
    assert us["aws_region"] == "us-east-1"
    assert us["available_model_count"] == 3  # legacy excluded
    assert us["configured_model_count"] == 1
    assert us["missing_model_count"] == 2
    missing_ids = {m["model_id"] for m in us["missing_models"]}
    assert missing_ids == {
        "anthropic.claude-opus-4-7",
        "amazon.nova-pro-v1:0",
    }
    # Legacy/INACTIVE upstream model must never appear in any market.
    for market in body["markets"]:
        for m in market["missing_models"]:
            assert m["model_id"] != "legacy.dont-show-me"

    # EU and AU have no contributing regions, so everything available is "missing".
    assert by_market["EU"]["missing_model_count"] == by_market["EU"]["available_model_count"]
    assert by_market["AU"]["missing_model_count"] == by_market["AU"]["available_model_count"]


def test_missing_models_ignores_non_bedrock_providers(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")

    deployed = _model_info_with_bedrock(
        [
            "openai/gpt-4o",
            "vertex_ai/gemini-1.5-pro",
            "azure/gpt-4",
            # Only this one should be counted as configured.
            "bedrock/us.amazon.nova-pro-v1:0",
        ]
    )

    with patch("app.api.public.LiteLLMService") as mock_service_cls, _patch_catalog_fetch(
        _upstream_catalog()
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(return_value=deployed)
        response = client.get("/public/models/missing")

    assert response.status_code == 200
    by_market = {m["market"]: m for m in response.json()["markets"]}
    assert by_market["US"]["configured_model_count"] == 1
    missing_us = {m["model_id"] for m in by_market["US"]["missing_models"]}
    assert "amazon.nova-pro-v1:0" not in missing_us


def test_missing_models_admin_includes_dedicated_regions(client, db):
    _clear_bedrock_catalog_cache()
    public_region = _make_public_region(db, "us-east-public", suffix="us-pub")
    dedicated_region = _make_dedicated_region(db, "us-east-private", suffix="us-priv")

    public_deployed = _model_info_with_bedrock(
        ["bedrock/us.amazon.nova-pro-v1:0"]
    )
    private_deployed = _model_info_with_bedrock(
        ["bedrock/us.anthropic.claude-opus-4-7"]
    )

    def _service_factory(api_url, api_key):
        service = AsyncMock()
        if api_url == public_region.litellm_api_url:
            service.get_model_info = AsyncMock(return_value=public_deployed)
        elif api_url == dedicated_region.litellm_api_url:
            service.get_model_info = AsyncMock(return_value=private_deployed)
        else:
            service.get_model_info = AsyncMock(return_value={"data": []})
        return service

    admin, password = _make_admin_user(db)
    token = _get_token(client, admin.email, password)

    with patch(
        "app.api.public.LiteLLMService", side_effect=_service_factory
    ), _patch_catalog_fetch(_upstream_catalog()):
        response = client.get(
            "/public/models/missing",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    body = response.json()
    assert body["is_authenticated"] is True
    by_market = {m["market"]: m for m in body["markets"]}
    us = by_market["US"]
    # Admin sees both regions, so both Opus and Nova are configured.
    assert us["configured_model_count"] == 2
    assert sorted(us["regions"]) == ["us-east-private", "us-east-public"]
    missing_us = {m["model_id"] for m in us["missing_models"]}
    assert missing_us == {"meta.llama3-1-70b-instruct-v1:0"}


def test_missing_models_anonymous_excludes_dedicated_regions(client, db):
    _clear_bedrock_catalog_cache()
    public_region = _make_public_region(db, "us-east-public", suffix="us-pub")
    dedicated_region = _make_dedicated_region(db, "us-east-private", suffix="us-priv")

    def _service_factory(api_url, api_key):
        service = AsyncMock()
        if api_url == public_region.litellm_api_url:
            service.get_model_info = AsyncMock(
                return_value=_model_info_with_bedrock(
                    ["bedrock/us.amazon.nova-pro-v1:0"]
                )
            )
        elif api_url == dedicated_region.litellm_api_url:
            # If this fires anonymously, the test should fail.
            raise AssertionError("Dedicated region must not be queried anonymously")
        return service

    with patch(
        "app.api.public.LiteLLMService", side_effect=_service_factory
    ), _patch_catalog_fetch(_upstream_catalog()):
        response = client.get("/public/models/missing")

    assert response.status_code == 200
    by_market = {m["market"]: m for m in response.json()["markets"]}
    us = by_market["US"]
    assert us["configured_model_count"] == 1  # only public region counted
    assert us["regions"] == ["us-east-public"]


def test_missing_models_handles_unavailable_region(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")

    with patch("app.api.public.LiteLLMService") as mock_service_cls, _patch_catalog_fetch(
        _upstream_catalog()
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(
            side_effect=httpx.ConnectError("boom")
        )
        response = client.get("/public/models/missing")

    assert response.status_code == 200
    by_market = {m["market"]: m for m in response.json()["markets"]}
    # No region contributed, so everything available counts as missing.
    assert by_market["US"]["configured_model_count"] == 0
    assert by_market["US"]["missing_model_count"] == by_market["US"]["available_model_count"]


def test_missing_models_cache_control_anonymous_is_public(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    with patch("app.api.public.LiteLLMService") as mock_service_cls, _patch_catalog_fetch(
        _upstream_catalog()
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_with_bedrock([])
        )
        response = client.get("/public/models/missing")

    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("public")


def test_missing_models_cache_control_authenticated_is_private(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    admin, password = _make_admin_user(db, email="missing-admin-cache@example.com")
    token = _get_token(client, admin.email, password)

    with patch("app.api.public.LiteLLMService") as mock_service_cls, _patch_catalog_fetch(
        _upstream_catalog()
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_with_bedrock([])
        )
        response = client.get(
            "/public/models/missing",
            headers={"Authorization": f"Bearer {token}"},
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"].startswith("private")
