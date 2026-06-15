"""Tests for the /models/missing/{provider} endpoint family."""

import json
from datetime import datetime, UTC
from unittest.mock import AsyncMock, patch
from urllib.error import URLError

import httpx

from app.api import public as public_api
from app.core.security import get_password_hash
from app.db.models import DBRegion, DBUser

AWS_PATH = "/models/missing/aws"


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


# ---------------------------------------------------------------------------
# Provider dispatcher
# ---------------------------------------------------------------------------


def test_missing_models_unknown_provider_returns_404(client, db):
    _clear_bedrock_catalog_cache()
    admin, password = _make_admin_user(db, email="unknown-provider-admin@example.com")
    token = _get_token(client, admin.email, password)
    response = client.get(
        "/models/missing/lambda", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 404
    assert "Unknown provider" in response.json()["detail"]


def test_missing_models_google_returns_501(client, db):
    _clear_bedrock_catalog_cache()
    admin, password = _make_admin_user(db, email="google-missing-admin@example.com")
    token = _get_token(client, admin.email, password)
    response = client.get(
        "/models/missing/google", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 501
    assert "google" in response.json()["detail"].lower()


def test_missing_models_azure_returns_501(client, db):
    _clear_bedrock_catalog_cache()
    admin, password = _make_admin_user(db, email="azure-missing-admin@example.com")
    token = _get_token(client, admin.email, password)
    response = client.get(
        "/models/missing/azure", headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 501


def test_missing_models_requires_bearer_token(client, db):
    _clear_bedrock_catalog_cache()
    response = client.get(AWS_PATH)
    assert response.status_code == 401


def test_missing_models_provider_lookup_is_case_insensitive(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    admin, password = _make_admin_user(db, email="case-insensitive-admin@example.com")
    token = _get_token(client, admin.email, password)
    with (
        patch("app.api.public.LiteLLMService") as mock_service_cls,
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_with_bedrock([])
        )
        response = client.get(
            "/models/missing/AWS", headers={"Authorization": f"Bearer {token}"}
        )
    assert response.status_code == 200
    assert response.json()["provider"] == "aws"


# ---------------------------------------------------------------------------
# AWS report content
# ---------------------------------------------------------------------------


def test_missing_aws_reports_gaps_per_region_group(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    user, password = _make_admin_user(db, email="missing-report-admin@example.com")
    token = _get_token(client, user.email, password)

    deployed = _model_info_with_bedrock(
        [
            # US has only Llama deployed; missing Opus + Nova.
            "bedrock/us.meta.llama3-1-70b-instruct-v1:0",
        ]
    )

    with (
        patch("app.api.public.LiteLLMService") as mock_service_cls,
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(return_value=deployed)
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["provider"] == "aws"
    assert body["is_authenticated"] is True
    assert body["models_url"].startswith("http")

    by_group = {g["region_group"]: g for g in body["region_groups"]}
    assert set(by_group) == {"US", "EU", "AU"}

    us = by_group["US"]
    assert us["upstream_region"] == "us-east-1"
    assert us["available_model_count"] == 3  # legacy excluded
    assert us["configured_model_count"] == 1
    assert us["missing_model_count"] == 2
    missing_ids = {m["model_id"] for m in us["missing_models"]}
    assert missing_ids == {
        "anthropic.claude-opus-4-7",
        "amazon.nova-pro-v1:0",
    }
    # Legacy/INACTIVE upstream model must never appear in any region group.
    for group in body["region_groups"]:
        for m in group["missing_models"]:
            assert m["model_id"] != "legacy.dont-show-me"

    # EU and AU have no contributing regions, so everything available is "missing".
    assert (
        by_group["EU"]["missing_model_count"] == by_group["EU"]["available_model_count"]
    )
    assert (
        by_group["AU"]["missing_model_count"] == by_group["AU"]["available_model_count"]
    )


def test_missing_aws_ignores_non_bedrock_providers(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    admin, password = _make_admin_user(db, email="non-bedrock-admin@example.com")
    token = _get_token(client, admin.email, password)

    deployed = _model_info_with_bedrock(
        [
            "openai/gpt-4o",
            "vertex_ai/gemini-1.5-pro",
            "azure/gpt-4",
            # Only this one should be counted as configured.
            "bedrock/us.amazon.nova-pro-v1:0",
        ]
    )

    with (
        patch("app.api.public.LiteLLMService") as mock_service_cls,
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(return_value=deployed)
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    by_group = {g["region_group"]: g for g in response.json()["region_groups"]}
    assert by_group["US"]["configured_model_count"] == 1
    missing_us = {m["model_id"] for m in by_group["US"]["missing_models"]}
    assert "amazon.nova-pro-v1:0" not in missing_us


def test_missing_aws_admin_includes_dedicated_regions(client, db):
    _clear_bedrock_catalog_cache()
    public_region = _make_public_region(db, "us-east-public", suffix="us-pub")
    dedicated_region = _make_dedicated_region(db, "us-east-private", suffix="us-priv")

    public_deployed = _model_info_with_bedrock(["bedrock/us.amazon.nova-pro-v1:0"])
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

    with (
        patch("app.api.public.LiteLLMService", side_effect=_service_factory),
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    body = response.json()
    assert body["is_authenticated"] is True
    by_group = {g["region_group"]: g for g in body["region_groups"]}
    us = by_group["US"]
    assert us["configured_model_count"] == 2
    assert sorted(us["regions"]) == ["us-east-private", "us-east-public"]
    missing_us = {m["model_id"] for m in us["missing_models"]}
    assert missing_us == {"meta.llama3-1-70b-instruct-v1:0"}


def test_missing_aws_non_admin_excludes_dedicated_regions(client, db):
    _clear_bedrock_catalog_cache()
    public_region = _make_public_region(db, "us-east-public", suffix="us-pub")
    dedicated_region = _make_dedicated_region(db, "us-east-private", suffix="us-priv")

    password = "UserPassword123"
    user = DBUser(
        email="missing-user@example.com",
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=False,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    token = _get_token(client, user.email, password)

    def _service_factory(api_url, api_key):
        service = AsyncMock()
        if api_url == public_region.litellm_api_url:
            service.get_model_info = AsyncMock(
                return_value=_model_info_with_bedrock(
                    ["bedrock/us.amazon.nova-pro-v1:0"]
                )
            )
        elif api_url == dedicated_region.litellm_api_url:
            raise AssertionError("Dedicated region must not be queried anonymously")
        return service

    with (
        patch("app.api.public.LiteLLMService", side_effect=_service_factory),
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert response.json()["is_authenticated"] is True
    by_group = {g["region_group"]: g for g in response.json()["region_groups"]}
    us = by_group["US"]
    assert us["configured_model_count"] == 1
    assert us["regions"] == ["us-east-public"]


def test_missing_aws_handles_unavailable_region(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    admin, password = _make_admin_user(db, email="unavailable-region-admin@example.com")
    token = _get_token(client, admin.email, password)

    with (
        patch("app.api.public.LiteLLMService") as mock_service_cls,
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(
            side_effect=httpx.ConnectError("boom")
        )
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    by_group = {g["region_group"]: g for g in response.json()["region_groups"]}
    assert by_group["US"]["configured_model_count"] == 0
    assert (
        by_group["US"]["missing_model_count"] == by_group["US"]["available_model_count"]
    )


# ---------------------------------------------------------------------------
# Cache-Control headers
# ---------------------------------------------------------------------------


def test_missing_aws_cache_control_is_not_publicly_cacheable(client, db):
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="us")
    admin, password = _make_admin_user(db, email="cache-header-admin@example.com")
    token = _get_token(client, admin.email, password)
    with (
        patch("app.api.public.LiteLLMService") as mock_service_cls,
        _patch_catalog_fetch(_upstream_catalog()),
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_with_bedrock([])
        )
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    assert (
        response.headers["Cache-Control"]
        == "no-store, no-cache, must-revalidate, private"
    )


# ---------------------------------------------------------------------------
# Alias-based matching
# ---------------------------------------------------------------------------


def _upstream_catalog_with_qwen():
    """Upstream catalog containing a model that is deployed under a short alias."""
    return [
        {
            "modelId": "qwen.qwen3-32b-v1:0",
            "modelName": "Qwen3 32B (dense)",
            "providerName": "Qwen",
            "regions": ["us-east-1", "eu-central-1"],
            "modelLifecycle": {"status": "ACTIVE"},
            "modelCard": {
                "modelCardUrl": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html"
            },
        },
        {
            "modelId": "anthropic.claude-opus-4-7",
            "modelName": "Claude Opus 4.7",
            "providerName": "Anthropic",
            "regions": ["us-east-1"],
            "modelLifecycle": {"status": "ACTIVE"},
        },
    ]


def _model_info_with_aliases(provider_id, model_name, model_key):
    """Build a fake LiteLLM model_info payload with model_info fields that produce aliases."""
    return {
        "data": [
            {
                "model_name": model_name,
                "litellm_params": {"model": provider_id},
                "model_info": {
                    "litellm_provider": "bedrock",
                    "key": model_key,
                    "model": model_key,
                },
            }
        ]
    }


def test_missing_aws_alias_match_excludes_model(client, db):
    """A model deployed under a short name whose aliases contain the upstream ID is not missing."""
    _clear_bedrock_catalog_cache()
    _make_public_region(db, "us-east-1", suffix="alias-us")
    admin, password = _make_admin_user(db, email="alias-admin@example.com")
    token = _get_token(client, admin.email, password)

    # Model deployed as "bedrock/us.qwen3-32b" with model_info containing
    # the full upstream ID "qwen.qwen3-32b-v1:0" which _extract_aliases picks up.
    deployed = _model_info_with_aliases(
        provider_id="bedrock/us.qwen3-32b",
        model_name="qwen3-32b",
        model_key="qwen.qwen3-32b-v1:0",
    )

    with (
        patch("app.api.public.LiteLLMService") as mock_service_cls,
        _patch_catalog_fetch(_upstream_catalog_with_qwen()),
    ):
        mock_service_cls.return_value.get_model_info = AsyncMock(return_value=deployed)
        response = client.get(AWS_PATH, headers={"Authorization": f"Bearer {token}"})

    assert response.status_code == 200
    by_group = {g["region_group"]: g for g in response.json()["region_groups"]}
    us = by_group["US"]
    missing_ids = {m["model_id"] for m in us["missing_models"]}
    # qwen.qwen3-32b-v1:0 should NOT be missing because the alias matches.
    assert "qwen.qwen3-32b-v1:0" not in missing_ids
    # anthropic.claude-opus-4-7 IS missing since it's not deployed.
    assert "anthropic.claude-opus-4-7" in missing_ids


# ---------------------------------------------------------------------------
# Script-level tests (check_missing_models.py)
# ---------------------------------------------------------------------------


def test_build_diff_uses_model_card_urls_for_slack_hyperlinks():
    """build_diff enriches Slack output with hyperlinks from model_card_urls lookup."""
    import runpy
    from pathlib import Path

    _module = runpy.run_path(
        str(
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "check_missing_models.py"
        )
    )
    build_diff = _module["build_diff"]

    report = {
        "provider": "aws",
        "generated_at": "2025-01-01T00:00:00Z",
        "models_url": "https://example.com/models.json",
        "is_authenticated": True,
        "region_groups": [
            {
                "region_group": "US",
                "upstream_region": "us-east-1",
                "regions": ["us-east-1"],
                "available_model_count": 2,
                "configured_model_count": 0,
                "missing_model_count": 2,
                "missing_models": [
                    {
                        "model_id": "qwen.qwen3-32b-v1:0",
                        "model_name": "Qwen3 32B (dense)",
                        "provider_name": "Qwen",
                    },
                    {
                        "model_id": "anthropic.claude-opus-4-7",
                        "model_name": "Claude Opus 4.7",
                        "provider_name": "Anthropic",
                    },
                ],
            }
        ],
    }

    model_card_urls = {
        "qwen.qwen3-32b-v1:0": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html",
    }

    diff = build_diff(report, {"region_groups": {}}, model_card_urls)
    summary = diff["slack_summary"]

    # Qwen should have a Slack hyperlink
    assert (
        "<https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html|Qwen3 32B (dense)>"
        in summary
    )
    # Claude should be plain text (no URL available)
    assert "(Anthropic / Claude Opus 4.7)" in summary


def test_build_diff_works_without_model_card_urls():
    """build_diff falls back to plain text when no model_card_urls provided."""
    import runpy
    from pathlib import Path

    _module = runpy.run_path(
        str(
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "check_missing_models.py"
        )
    )
    build_diff = _module["build_diff"]

    report = {
        "provider": "aws",
        "generated_at": "2025-01-01T00:00:00Z",
        "models_url": "https://example.com/models.json",
        "is_authenticated": True,
        "region_groups": [
            {
                "region_group": "US",
                "upstream_region": "us-east-1",
                "regions": ["us-east-1"],
                "available_model_count": 1,
                "configured_model_count": 0,
                "missing_model_count": 1,
                "missing_models": [
                    {
                        "model_id": "qwen.qwen3-32b-v1:0",
                        "model_name": "Qwen3 32B (dense)",
                        "provider_name": "Qwen",
                    },
                ],
            }
        ],
    }

    # No model_card_urls passed
    diff = build_diff(report, {"region_groups": {}})
    summary = diff["slack_summary"]
    assert "(Qwen / Qwen3 32B (dense))" in summary
    # No hyperlinks
    assert "<http" not in summary


def test_fetch_model_card_urls_parses_catalog():
    """fetch_model_card_urls extracts modelCardUrl from the upstream catalog."""
    import runpy
    from pathlib import Path
    from unittest.mock import patch as mock_patch
    from io import BytesIO

    _module = runpy.run_path(
        str(
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "check_missing_models.py"
        )
    )
    fetch_model_card_urls = _module["fetch_model_card_urls"]

    catalog = json.dumps(
        [
            {
                "modelId": "qwen.qwen3-32b-v1:0",
                "modelCard": {
                    "modelCardUrl": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html"
                },
            },
            {
                "modelId": "anthropic.claude-opus-4-7",
                "modelCard": None,
            },
            {
                "modelId": "meta.llama3-1-70b-instruct-v1:0",
            },
        ]
    ).encode()

    with mock_patch("urllib.request.urlopen") as mock_urlopen:
        mock_urlopen.return_value.__enter__ = lambda s: BytesIO(catalog)
        mock_urlopen.return_value.__exit__ = lambda s, *a: None
        urls = fetch_model_card_urls("https://example.com/models.json")

    assert urls == {
        "qwen.qwen3-32b-v1:0": "https://docs.aws.amazon.com/bedrock/latest/userguide/model-card-qwen-qwen3-32b.html",
    }


def test_fetch_model_card_urls_returns_empty_on_failure():
    """fetch_model_card_urls returns {} on network errors."""
    import runpy
    from pathlib import Path
    from unittest.mock import patch as mock_patch

    _module = runpy.run_path(
        str(
            Path(__file__).resolve().parent.parent
            / "scripts"
            / "check_missing_models.py"
        )
    )
    fetch_model_card_urls = _module["fetch_model_card_urls"]

    with mock_patch("urllib.request.urlopen", side_effect=URLError("boom")):
        urls = fetch_model_card_urls("https://example.com/models.json")

    assert urls == {}
