import asyncio

import pytest
import httpx
from unittest.mock import AsyncMock, MagicMock, patch

from app.api import public as public_api
from app.db.models import DBRegion, DBTeam, DBTeamRegion, DBUser
from app.core.security import get_password_hash


def _clear_public_models_cache():
    public_api._models_cache["data"] = []
    public_api._models_cache["expires_at"] = public_api.datetime.min.replace(
        tzinfo=public_api.UTC
    )
    public_api._dedicated_cache["by_team"] = {}
    public_api._dedicated_cache["team_expires"] = {}


@pytest.fixture(autouse=True)
def _offline_bedrock_catalog():
    """Keep /public/models off the network.

    The endpoint enriches models with EOL dates from the upstream Bedrock
    catalog; an empty BEDROCK_MODELS_URL skips that fetch. Tests that exercise
    the EOL path patch _get_bedrock_eol_index (or this setting) themselves.
    """
    with patch.object(public_api.settings, "BEDROCK_MODELS_URL", ""):
        yield


def test_public_models_returns_aggregated_data(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="eu-central-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()
    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "claude-3-5-sonnet-20241022",
                        "litellm_params": {"aws_region_name": "eu-central-1"},
                        "model_info": {
                            "max_input_tokens": 200000,
                            "litellm_provider": "bedrock_converse",
                            "mode": "chat",
                            "metadata": "Anthropic's most capable model. Excellent for complex reasoning, analysis, and large context windows.",
                        },
                    }
                ]
            }
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        assert response.headers["Cache-Control"] == "public, max-age=3600"
        data = response.json()
        assert len(data) >= 1
        first_region = data[0]
        assert first_region["region"] == "eu-central-1"
        assert first_region["status"] == "ga"
        assert len(first_region["models"]) >= 1

        first_model = first_region["models"][0]
        assert first_model["model_id"] == "claude-3-5-sonnet-20241022"
        assert first_model["provider"] == "aws"
        assert first_model["type"] == "chat"
        assert first_model["context_length"] == 200000
        assert (
            first_model["metadata_raw"]
            == "Anthropic's most capable model. Excellent for complex reasoning, analysis, and large context windows."
        )
        assert "claude-3-5" in first_model["aliases"]
        assert "description" in first_model
        assert "Strengths:" in first_model["description"]
        assert first_model["manufacturer"]["name"] == "Anthropic"
        assert first_model["manufacturer"]["website"] == "https://www.anthropic.com"
        assert first_model["manufacturer"]["release_date"] == "2024-10-22"
        assert "max_output_tokens" in first_model
        assert first_model["capabilities"]["supports_function_calling"] is False
        assert "pricing" in first_model


def test_public_models_includes_unavailable_region(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="us-east-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()
    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            side_effect=httpx.ConnectError("connection refused")
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(
            item["status"] == "unavailable" and item["region"] == "us-east-1"
            for item in data
        )


def test_public_models_pricing_numeric_values(client, db):
    """Pricing fields are correctly propagated when LiteLLM returns numeric values."""
    _clear_public_models_cache()
    region = DBRegion(
        name="ap-southeast-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "supports_prompt_caching": True,
                            "input_cost_per_token": 0.000005,
                            "output_cost_per_token": 0.000015,
                            "cache_creation_input_token_cost": 0.00000625,
                            "cache_creation_input_token_cost_above_1hr": 0.00001,
                            "cache_read_input_token_cost": 0.0000005,
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(
            return_value={"values": {"global": 0.2}}
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "ap-southeast-1")
        pricing = region_data["models"][0]["pricing"]
        assert pricing["input_cost_per_token"] == pytest.approx(0.000006)
        assert pricing["output_cost_per_token"] == pytest.approx(0.000018)
        assert pricing["input_cost_per_million_tokens"] == pytest.approx(6.0)
        assert pricing["output_cost_per_million_tokens"] == pytest.approx(18.0)
        assert pricing["cache_creation_input_cost_per_million_tokens"] == pytest.approx(
            7.5
        )
        assert pricing[
            "cache_creation_input_cost_above_1hr_per_million_tokens"
        ] == pytest.approx(12.0)
        assert pricing["cache_read_input_cost_per_million_tokens"] == pytest.approx(0.6)


def test_public_models_pricing_uses_litellm_global_margin(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="sa-east-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "supports_prompt_caching": True,
                            "input_cost_per_token": 0.000005,
                            "output_cost_per_token": 0.000015,
                            "cache_creation_input_token_cost": 0.00000625,
                            "cache_creation_input_token_cost_above_1hr": 0.00001,
                            "cache_read_input_token_cost": 0.0000005,
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(
            return_value={"values": {"global": 0.5}}
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "sa-east-1")
        pricing = region_data["models"][0]["pricing"]
        assert pricing["input_cost_per_token"] == pytest.approx(0.0000075)
        assert pricing["output_cost_per_token"] == pytest.approx(0.0000225)
        assert pricing["input_cost_per_million_tokens"] == pytest.approx(7.5)
        assert pricing["output_cost_per_million_tokens"] == pytest.approx(22.5)
        assert pricing["cache_creation_input_cost_per_million_tokens"] == pytest.approx(
            9.375
        )
        assert pricing[
            "cache_creation_input_cost_above_1hr_per_million_tokens"
        ] == pytest.approx(15.0)
        assert pricing["cache_read_input_cost_per_million_tokens"] == pytest.approx(
            0.75
        )


def test_public_models_pricing_falls_back_to_default_margin(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="ca-central-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "supports_prompt_caching": True,
                            "input_cost_per_token": 0.000005,
                            "output_cost_per_token": 0.000015,
                            "cache_creation_input_token_cost": 0.00000625,
                            "cache_creation_input_token_cost_above_1hr": 0.00001,
                            "cache_read_input_token_cost": 0.0000005,
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(return_value={"values": {}})

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "ca-central-1")
        pricing = region_data["models"][0]["pricing"]
        assert pricing["input_cost_per_token"] == pytest.approx(0.000006)
        assert pricing["output_cost_per_token"] == pytest.approx(0.000018)
        assert pricing["input_cost_per_million_tokens"] == pytest.approx(6.0)
        assert pricing["output_cost_per_million_tokens"] == pytest.approx(18.0)
        assert pricing["cache_creation_input_cost_per_million_tokens"] == pytest.approx(
            7.5
        )
        assert pricing[
            "cache_creation_input_cost_above_1hr_per_million_tokens"
        ] == pytest.approx(12.0)
        assert pricing["cache_read_input_cost_per_million_tokens"] == pytest.approx(0.6)


def test_public_models_pricing_missing_values(client, db):
    """Pricing fields are null when LiteLLM does not return cost info."""
    _clear_public_models_cache()
    region = DBRegion(
        name="ap-northeast-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {},
                        "model_info": {"mode": "chat"},
                    }
                ]
            }
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "ap-northeast-1")
        pricing = region_data["models"][0]["pricing"]
        assert pricing["input_cost_per_token"] is None
        assert pricing["output_cost_per_token"] is None
        assert pricing["input_cost_per_million_tokens"] is None
        assert pricing["output_cost_per_million_tokens"] is None
        assert pricing["cache_creation_input_cost_per_million_tokens"] is None
        assert pricing["cache_creation_input_cost_above_1hr_per_million_tokens"] is None
        assert pricing["cache_read_input_cost_per_million_tokens"] is None


def test_public_models_pricing_non_numeric_values(client, db):
    """Non-numeric pricing values from LiteLLM are coerced to null without raising."""
    _clear_public_models_cache()
    region = DBRegion(
        name="eu-west-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "input_cost_per_token": "n/a",
                            "output_cost_per_token": "n/a",
                            "cache_creation_input_token_cost": "n/a",
                            "cache_creation_input_token_cost_above_1hr": "n/a",
                            "cache_read_input_token_cost": "n/a",
                        },
                    }
                ]
            }
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "eu-west-1")
        pricing = region_data["models"][0]["pricing"]
        assert pricing["input_cost_per_token"] is None
        assert pricing["output_cost_per_token"] is None
        assert pricing["input_cost_per_million_tokens"] is None
        assert pricing["output_cost_per_million_tokens"] is None
        assert pricing["cache_creation_input_cost_per_million_tokens"] is None
        assert pricing["cache_creation_input_cost_above_1hr_per_million_tokens"] is None
        assert pricing["cache_read_input_cost_per_million_tokens"] is None


def test_prompt_caching_cache_pricing_nulled_when_not_supported(client, db):
    """Cache pricing fields are null when supports_prompt_caching is False."""
    _clear_public_models_cache()
    region = DBRegion(
        name="caching-unsupported-region",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "gpt-4o",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "supports_prompt_caching": False,
                            # LiteLLM static DB may still return cache costs — should be nulled
                            "cache_creation_input_token_cost": 0.000003,
                            "cache_read_input_token_cost": 0.0000003,
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(
            return_value={"values": {"global": 0.0}}
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(
            r for r in data if r["region"] == "caching-unsupported-region"
        )
        model = region_data["models"][0]
        assert model["capabilities"]["supports_prompt_caching"] is False
        assert model["pricing"]["cache_creation_input_cost_per_million_tokens"] is None
        assert (
            model["pricing"]["cache_creation_input_cost_above_1hr_per_million_tokens"]
            is None
        )
        assert model["pricing"]["cache_read_input_cost_per_million_tokens"] is None


def test_prompt_caching_cache_pricing_present_when_supported(client, db):
    """Cache pricing fields are populated when supports_prompt_caching is True."""
    _clear_public_models_cache()
    region = DBRegion(
        name="caching-enabled-region",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "claude-3-sonnet",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "supports_prompt_caching": True,
                            "cache_creation_input_token_cost": 0.000003,
                            "cache_creation_input_token_cost_above_1hr": 0.000006,
                            "cache_read_input_token_cost": 0.0000003,
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(
            return_value={"values": {"global": 0.0}}
        )

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "caching-enabled-region")
        model = region_data["models"][0]
        assert model["capabilities"]["supports_prompt_caching"] is True
        assert model["pricing"][
            "cache_creation_input_cost_per_million_tokens"
        ] == pytest.approx(3.0)
        assert model["pricing"][
            "cache_creation_input_cost_above_1hr_per_million_tokens"
        ] == pytest.approx(6.0)
        assert model["pricing"][
            "cache_read_input_cost_per_million_tokens"
        ] == pytest.approx(0.3)


def test_public_models_uses_region_key_for_model_info(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="us-west-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="sk-region",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(return_value={"data": []})

        response = client.get("/public/models")
        assert response.status_code == 200
        mock_service_cls.assert_called_once_with(
            api_url="https://litellm.example", api_key="sk-region"
        )


def test_public_models_filters_by_alias(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="us-central-1",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {"model_name": "gpt-4o", "model_info": {"mode": "chat"}},
                    {
                        "model_name": "claude-3-5-sonnet-20241022",
                        "model_info": {"mode": "chat"},
                    },
                ]
            }
        )

        response = client.get("/public/models?alias=gpt-4")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "us-central-1")
        assert len(region_data["models"]) == 1
        assert region_data["models"][0]["model_id"] == "gpt-4o"


def test_public_models_filters_by_comma_separated_aliases(client, db):
    _clear_public_models_cache()
    region = DBRegion(
        name="us-central-2",
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://litellm.example",
        litellm_api_key="key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()

    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {"model_name": "gpt-4o", "model_info": {"mode": "chat"}},
                    {
                        "model_name": "claude-3-5-sonnet-20241022",
                        "model_info": {"mode": "chat"},
                    },
                ]
            }
        )

        response = client.get("/public/models?alias=gpt-4,claude-3-5")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "us-central-2")
        returned_model_ids = sorted(
            [model["model_id"] for model in region_data["models"]]
        )
        assert returned_model_ids == ["claude-3-5-sonnet-20241022", "gpt-4o"]


# ---------------------------------------------------------------------------
# Authenticated /public/models tests
# ---------------------------------------------------------------------------


def _make_team_user(db, team, email="teamuser_auth@example.com"):
    """Create a team user and return (user, password)."""
    password = "TestPassword123"
    user = DBUser(
        email=email,
        hashed_password=get_password_hash(password),
        is_active=True,
        is_admin=False,
        team_id=team.id,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user, password


def _make_admin_user(db, email="adminauth@example.com"):
    """Create an admin user and return (user, password)."""
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


def _make_dedicated_region(db, name):
    region = DBRegion(
        name=name,
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://dedicated.example",
        litellm_api_key="dedicated-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _make_public_region(db, name):
    region = DBRegion(
        name=name,
        postgres_host="host",
        postgres_port=5432,
        postgres_admin_user="user",
        postgres_admin_password="pass",
        litellm_api_url="https://public.example",
        litellm_api_key="public-key",
        is_active=True,
        is_dedicated=False,
    )
    db.add(region)
    db.commit()
    db.refresh(region)
    return region


def _model_info_response(model_name="gpt-4o"):
    return {
        "data": [
            {
                "model_name": model_name,
                "litellm_params": {},
                "model_info": {"mode": "chat"},
            }
        ]
    }


def test_public_models_unauthenticated_cache_control_is_public(client, db):
    """Unauthenticated requests get Cache-Control: public."""
    _clear_public_models_cache()
    _make_public_region(db, "cache-control-public-region")

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_response()
        )
        response = client.get("/public/models")

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "public, max-age=3600"


def test_public_models_authenticated_cache_control_is_private(client, db):
    """Authenticated requests get Cache-Control: private."""
    _clear_public_models_cache()
    team = DBTeam(
        name="Cache Control Team",
        admin_email="cachecontrol@example.com",
        phone="0000000000",
        billing_address="1 Test St",
        is_active=True,
        budget_type="periodic",
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    user, password = _make_team_user(db, team, email="cachecontroluser@example.com")
    token = _get_token(client, user.email, password)

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_response()
        )
        response = client.get(
            "/public/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    assert response.headers["Cache-Control"] == "private, max-age=3600"


def test_public_models_team_member_sees_dedicated_regions(client, db):
    """A team member gets their team's dedicated region in the response."""
    _clear_public_models_cache()
    team = DBTeam(
        name="Dedicated Team",
        admin_email="dedicatedteam@example.com",
        phone="0000000000",
        billing_address="1 Test St",
        is_active=True,
        budget_type="periodic",
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    dedicated_region = _make_dedicated_region(db, "team-dedicated-region")
    db.add(DBTeamRegion(team_id=team.id, region_id=dedicated_region.id))
    db.commit()

    user, password = _make_team_user(db, team, email="dedicatedteamuser@example.com")
    token = _get_token(client, user.email, password)

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_response("dedicated-model")
        )
        response = client.get(
            "/public/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    data = response.json()
    region_names = [r["region"] for r in data]
    assert "team-dedicated-region" in region_names
    dedicated = next(r for r in data if r["region"] == "team-dedicated-region")
    assert dedicated["status"] == "ga"
    assert len(dedicated["models"]) == 1
    assert dedicated["models"][0]["model_id"] == "dedicated-model"


def test_public_models_team_member_does_not_see_other_team_dedicated_regions(
    client, db
):
    """A team member does not see dedicated regions belonging to another team."""
    _clear_public_models_cache()
    team_a = DBTeam(
        name="Team A",
        admin_email="teama@example.com",
        phone="0000000001",
        billing_address="1 A St",
        is_active=True,
        budget_type="periodic",
    )
    team_b = DBTeam(
        name="Team B",
        admin_email="teamb@example.com",
        phone="0000000002",
        billing_address="1 B St",
        is_active=True,
        budget_type="periodic",
    )
    db.add_all([team_a, team_b])
    db.commit()
    db.refresh(team_a)
    db.refresh(team_b)

    region_a = _make_dedicated_region(db, "team-a-dedicated")
    region_b = _make_dedicated_region(db, "team-b-dedicated")
    db.add(DBTeamRegion(team_id=team_a.id, region_id=region_a.id))
    db.add(DBTeamRegion(team_id=team_b.id, region_id=region_b.id))
    db.commit()

    user_a, password_a = _make_team_user(db, team_a, email="usera_cross@example.com")
    token_a = _get_token(client, user_a.email, password_a)

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_response()
        )
        response = client.get(
            "/public/models", headers={"Authorization": f"Bearer {token_a}"}
        )

    assert response.status_code == 200
    region_names = [r["region"] for r in response.json()]
    assert "team-a-dedicated" in region_names
    assert "team-b-dedicated" not in region_names


def test_public_models_admin_sees_all_dedicated_regions(client, db):
    """An admin user sees all dedicated regions regardless of team."""
    _clear_public_models_cache()
    team_x = DBTeam(
        name="Team X",
        admin_email="teamx@example.com",
        phone="1111111111",
        billing_address="1 X St",
        is_active=True,
        budget_type="periodic",
    )
    team_y = DBTeam(
        name="Team Y",
        admin_email="teamy@example.com",
        phone="2222222222",
        billing_address="1 Y St",
        is_active=True,
        budget_type="periodic",
    )
    db.add_all([team_x, team_y])
    db.commit()
    db.refresh(team_x)
    db.refresh(team_y)

    region_x = _make_dedicated_region(db, "admin-dedicated-x")
    region_y = _make_dedicated_region(db, "admin-dedicated-y")
    db.add(DBTeamRegion(team_id=team_x.id, region_id=region_x.id))
    db.add(DBTeamRegion(team_id=team_y.id, region_id=region_y.id))
    db.commit()

    admin, password = _make_admin_user(db, email="adminseesall@example.com")
    token = _get_token(client, admin.email, password)

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_response()
        )
        response = client.get(
            "/public/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    region_names = [r["region"] for r in response.json()]
    assert "admin-dedicated-x" in region_names
    assert "admin-dedicated-y" in region_names


def test_public_models_team_visibility_uses_explicit_team_regions(client, db):
    """Team users only see regions explicitly assigned in team_regions."""
    _clear_public_models_cache()
    team = DBTeam(
        name="Hide Public Team",
        admin_email="hidepublic@example.com",
        phone="3333333333",
        billing_address="1 Hide St",
        is_active=True,
        budget_type="periodic",
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    _make_public_region(db, "public-region-hidden")
    dedicated_region = _make_dedicated_region(db, "team-hidden-dedicated")
    db.add(DBTeamRegion(team_id=team.id, region_id=dedicated_region.id))
    db.commit()

    user, password = _make_team_user(db, team, email="hidepublicuser@example.com")
    token = _get_token(client, user.email, password)

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_model_info_response()
        )
        response = client.get(
            "/public/models", headers={"Authorization": f"Bearer {token}"}
        )

    assert response.status_code == 200
    region_names = [r["region"] for r in response.json()]
    assert "public-region-hidden" not in region_names
    assert "team-hidden-dedicated" in region_names


# ---------------------------------------------------------------------------
# aliased_to
# ---------------------------------------------------------------------------


def _alias_group_response():
    """Three models on one upstream deployment, plus an unrelated canonical one.

    Mirrors DEV amazeeai-us1: aliases are separate entries sharing a
    litellm_params.model, one of them annotated "Points to ...".
    """
    return {
        "data": [
            {
                "model_name": "claude-4-6-sonnet",
                "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-6"},
                "model_info": {"mode": "chat", "metadata": "Previous generation."},
            },
            {
                "model_name": "claude-3-5-sonnet",
                "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-6"},
                "model_info": {
                    "mode": "chat",
                    "metadata": "Points to claude-4-6-sonnet.",
                },
            },
            {
                "model_name": "chat",
                "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-6"},
                "model_info": {
                    "mode": "chat",
                    "metadata": "Points to the latest Claude model.",
                },
            },
            {
                "model_name": "gemini-2.5-flash-image",
                "litellm_params": {"model": "vertex_ai/gemini-2.5-flash-image"},
                "model_info": {"mode": "chat", "metadata": "Image generation."},
            },
        ]
    }


def _models_by_id(response):
    return {m["model_id"]: m for m in response.json()[0]["models"]}


def test_public_models_marks_aliases_and_canonical_models(client, db):
    """Aliases point at the canonical model; the canonical one reports null."""
    _clear_public_models_cache()
    _make_public_region(db, "alias-region")

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_alias_group_response()
        )
        response = client.get("/public/models")

    assert response.status_code == 200
    models = _models_by_id(response)
    # Canonical: shares the upstream id's identity tokens.
    assert models["claude-4-6-sonnet"]["aliased_to"] is None
    # Alias resolved from the "Points to ..." annotation.
    assert models["claude-3-5-sonnet"]["aliased_to"] == "claude-4-6-sonnet"
    # Alias with vague prose still resolved via the shared upstream deployment.
    assert models["chat"]["aliased_to"] == "claude-4-6-sonnet"
    # Sole deployment on its upstream model: canonical by definition.
    assert models["gemini-2.5-flash-image"]["aliased_to"] is None


def test_public_models_aliased_to_is_null_when_canonical_is_ambiguous(client, db):
    """No single winner means null everywhere, never a guessed pointer."""
    _clear_public_models_cache()
    _make_public_region(db, "ambiguous-alias-region")

    upstream = {"model": "bedrock/us.anthropic.claude-sonnet-4-6"}
    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "house-chat",
                        "litellm_params": upstream,
                        "model_info": {"mode": "chat"},
                    },
                    {
                        "model_name": "house-chat-fast",
                        "litellm_params": upstream,
                        "model_info": {"mode": "chat"},
                    },
                ]
            }
        )
        response = client.get("/public/models")

    assert response.status_code == 200
    models = _models_by_id(response)
    assert models["house-chat"]["aliased_to"] is None
    assert models["house-chat-fast"]["aliased_to"] is None


def _router_settings_response(alias_map):
    return {
        "fields": [
            {"field_name": "routing_strategy", "field_value": "simple-shuffle"},
            {"field_name": "model_group_alias", "field_value": alias_map},
        ]
    }


def test_public_models_resolves_aliases_from_router_settings(client, db):
    """A real model_group_alias resolves groups the derived map cannot."""
    _clear_public_models_cache()
    _make_public_region(db, "router-alias-region")

    upstream = {"model": "bedrock/us.anthropic.claude-sonnet-4-6"}
    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "house-chat",
                        "litellm_params": upstream,
                        "model_info": {"mode": "chat"},
                    },
                    {
                        "model_name": "house-chat-fast",
                        "litellm_params": upstream,
                        "model_info": {"mode": "chat"},
                    },
                ]
            }
        )
        mock_cls.return_value.get_router_settings = AsyncMock(
            return_value=_router_settings_response({"house-chat": "house-chat-fast"})
        )
        response = client.get("/public/models")

    assert response.status_code == 200
    models = _models_by_id(response)
    assert models["house-chat"]["aliased_to"] == "house-chat-fast"
    assert models["house-chat-fast"]["aliased_to"] is None


def test_public_models_router_alias_wins_over_derived_map(client, db):
    """model_group_alias overrides what the fake-alias heuristics derived."""
    _clear_public_models_cache()
    _make_public_region(db, "router-priority-region")

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_alias_group_response()
        )
        mock_cls.return_value.get_router_settings = AsyncMock(
            return_value=_router_settings_response({"chat": "gemini-2.5-flash-image"})
        )
        response = client.get("/public/models")

    assert response.status_code == 200
    models = _models_by_id(response)
    # Router settings win for chat; the rest keep the derived resolution.
    assert models["chat"]["aliased_to"] == "gemini-2.5-flash-image"
    assert models["claude-3-5-sonnet"]["aliased_to"] == "claude-4-6-sonnet"


def test_public_models_falls_back_when_router_settings_unavailable(client, db):
    """A broken /router/settings degrades to the derived map, not an error."""
    _clear_public_models_cache()
    _make_public_region(db, "router-broken-region")

    with patch("app.api.public.LiteLLMService") as mock_cls:
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value=_alias_group_response()
        )
        mock_cls.return_value.get_router_settings = AsyncMock(
            side_effect=Exception("boom")
        )
        response = client.get("/public/models")

    assert response.status_code == 200
    assert response.json()[0]["status"] == "ga"
    models = _models_by_id(response)
    assert models["chat"]["aliased_to"] == "claude-4-6-sonnet"


def test_extract_model_group_alias_normalizes_shapes():
    """Direct unit check: string targets, item dicts, junk, self-aliases."""
    settings = _router_settings_response(
        {
            "chat": "claude-4-7-opus",
            "vision": {"model": "gemini-2.5-flash-image", "hidden": True},
            "self": "self",
            "junk": 42,
        }
    )
    assert public_api._extract_model_group_alias(settings) == {
        "chat": "claude-4-7-opus",
        "vision": "gemini-2.5-flash-image",
    }
    assert public_api._extract_model_group_alias(None) == {}
    assert public_api._extract_model_group_alias({"fields": "nope"}) == {}


def test_build_alias_map_ignores_word_order_and_version_suffixes():
    """Direct unit check of the name-shape fallback."""
    alias_map = public_api._build_alias_map(
        [
            {
                "model_name": "claude-4-5-haiku",
                "litellm_params": {
                    "model": "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
                },
                "model_info": {},
            },
            {
                "model_name": "claude-3-5-haiku",
                "litellm_params": {
                    "model": "bedrock/us.anthropic.claude-haiku-4-5-20251001-v1:0"
                },
                "model_info": {},
            },
            {
                "model_name": "titan-embed-text-v2:0",
                "litellm_params": {"model": "amazon.titan-embed-text-v2:0"},
                "model_info": {},
            },
            {
                "model_name": "embeddings",
                "litellm_params": {"model": "amazon.titan-embed-text-v2:0"},
                "model_info": {},
            },
        ]
    )
    assert alias_map == {
        "claude-3-5-haiku": "claude-4-5-haiku",
        "embeddings": "titan-embed-text-v2:0",
    }


# ---------------------------------------------------------------------------
# eol_date
# ---------------------------------------------------------------------------


def _eol_response():
    return {
        "data": [
            {
                "model_name": "claude-3-haiku",
                "litellm_params": {
                    "model": "bedrock/us.anthropic.claude-3-haiku-20240307-v1:0"
                },
                "model_info": {
                    "mode": "chat",
                    "metadata": "Low cost Claude model. (EOL: 2026-09-10)",
                },
            },
            {
                "model_name": "claude-4-6-sonnet",
                "litellm_params": {"model": "bedrock/us.anthropic.claude-sonnet-4-6"},
                "model_info": {"mode": "chat", "metadata": "Current Sonnet."},
            },
            {
                "model_name": "gemini-2.5-pro",
                "litellm_params": {"model": "vertex_ai/gemini-2.5-pro"},
                "model_info": {"mode": "chat", "metadata": "Not a Bedrock model."},
            },
        ]
    }


def test_public_models_reports_eol_from_annotation_and_catalog(client, db):
    """Manual annotations and upstream catalog dates both surface, tagged."""
    _clear_public_models_cache()
    _make_public_region(db, "eol-region")

    with (
        patch("app.api.public.LiteLLMService") as mock_cls,
        patch.object(
            public_api,
            "_get_bedrock_eol_index",
            AsyncMock(return_value={"anthropic.claude-sonnet-4-6": "2026-10-14"}),
        ),
    ):
        mock_cls.return_value.get_model_info = AsyncMock(return_value=_eol_response())
        response = client.get("/public/models")

    assert response.status_code == 200
    models = _models_by_id(response)

    assert models["claude-3-haiku"]["eol_date"] == "2026-09-10"
    assert models["claude-3-haiku"]["eol_source"] == "manual"
    # metadata_raw must keep the annotation: no breaking change.
    assert "(EOL: 2026-09-10)" in models["claude-3-haiku"]["metadata_raw"]

    assert models["claude-4-6-sonnet"]["eol_date"] == "2026-10-14"
    assert models["claude-4-6-sonnet"]["eol_source"] == "bedrock"

    assert models["gemini-2.5-pro"]["eol_date"] is None
    assert models["gemini-2.5-pro"]["eol_source"] is None


def test_public_models_annotation_overrides_catalog_eol(client, db):
    """An operator may retire a model earlier than AWS does."""
    _clear_public_models_cache()
    _make_public_region(db, "eol-override-region")

    with (
        patch("app.api.public.LiteLLMService") as mock_cls,
        patch.object(
            public_api,
            "_get_bedrock_eol_index",
            AsyncMock(
                return_value={"anthropic.claude-3-haiku-20240307-v1:0": "2026-09-10"}
            ),
        ),
    ):
        mock_cls.return_value.get_model_info = AsyncMock(
            return_value={
                "data": [
                    {
                        "model_name": "claude-3-haiku",
                        "litellm_params": {
                            "model": "bedrock/us.anthropic.claude-3-haiku-20240307-v1:0"
                        },
                        "model_info": {
                            "mode": "chat",
                            "metadata": "Retiring early. (EOL: 2026-08-01)",
                        },
                    }
                ]
            }
        )
        response = client.get("/public/models")

    models = _models_by_id(response)
    assert models["claude-3-haiku"]["eol_date"] == "2026-08-01"
    assert models["claude-3-haiku"]["eol_source"] == "manual"


def test_public_models_survives_unreachable_bedrock_catalog(client, db):
    """A dead upstream catalog must not fail the endpoint."""
    _clear_public_models_cache()
    _make_public_region(db, "eol-degraded-region")
    public_api._bedrock_catalog_cache["eol_index"] = {}

    with (
        patch("app.api.public.LiteLLMService") as mock_cls,
        patch.object(
            public_api.settings,
            "BEDROCK_MODELS_URL",
            "https://catalog.invalid/models.json",
        ),
        patch.object(
            public_api,
            "_fetch_bedrock_catalog",
            AsyncMock(side_effect=httpx.ConnectError("boom")),
        ),
    ):
        mock_cls.return_value.get_model_info = AsyncMock(return_value=_eol_response())
        response = client.get("/public/models")

    assert response.status_code == 200
    models = _models_by_id(response)
    # The manual annotation still works; the catalog-only date is simply absent.
    assert models["claude-3-haiku"]["eol_date"] == "2026-09-10"
    assert models["claude-4-6-sonnet"]["eol_date"] is None


def test_build_eol_index_prefers_lifecycle_and_parses_card_dates():
    """Both upstream date fields are read; lifecycle wins when both are set."""
    index = public_api._build_eol_index(
        [
            {
                "modelId": "anthropic.claude-sonnet-4-20250514-v1:0",
                "modelLifecycle": {"endOfLifeTime": "2026-10-14 07:00:00+00:00"},
                "modelCard": {"modelEolDate": "October 14, 2026"},
            },
            {
                "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
                "modelLifecycle": {"status": "ACTIVE"},
                "modelCard": {"modelEolDate": "September 10, 2026"},
            },
            {
                "modelId": "anthropic.claude-opus-5",
                "modelLifecycle": {"status": "ACTIVE"},
                "modelCard": {"modelEolDate": None},
            },
            {"modelId": "broken.model", "modelCard": {"modelEolDate": "not a date"}},
        ]
    )
    assert index == {
        "anthropic.claude-sonnet-4-20250514-v1:0": "2026-10-14",
        "anthropic.claude-3-haiku-20240307-v1:0": "2026-09-10",
    }


@pytest.mark.asyncio
async def test_bedrock_eol_index_is_derived_once_per_fetch():
    """A cold multi-region fan-out must not rebuild the index per region.

    The index is derived inside _fetch_bedrock_catalog's lock, so concurrent
    callers share one HTTP fetch and one derivation.
    """
    public_api._bedrock_catalog_cache["url"] = None
    public_api._bedrock_catalog_cache["data"] = None
    public_api._bedrock_catalog_cache["eol_index"] = {}
    public_api._bedrock_catalog_cache["expires_at"] = public_api.datetime.min.replace(
        tzinfo=public_api.UTC
    )

    catalog = [
        {
            "modelId": "anthropic.claude-3-haiku-20240307-v1:0",
            "modelLifecycle": {"endOfLifeTime": "2026-09-10 08:00:00+00:00"},
        }
    ]
    builds = []
    real_build = public_api._build_eol_index

    def counting_build(data):
        builds.append(len(data))
        return real_build(data)

    response = MagicMock()
    response.json.return_value = catalog
    response.raise_for_status.return_value = None
    mock_client = MagicMock()
    mock_client.get = AsyncMock(return_value=response)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with (
        patch.object(
            public_api.settings,
            "BEDROCK_MODELS_URL",
            "https://catalog.test/models.json",
        ),
        patch.object(public_api, "_build_eol_index", counting_build),
        patch.object(public_api.httpx, "AsyncClient", return_value=mock_client),
    ):
        indexes = await asyncio.gather(
            *(public_api._get_bedrock_eol_index() for _ in range(5))
        )

    assert len(builds) == 1, f"index rebuilt {len(builds)} times"
    assert mock_client.get.await_count == 1
    expected = {"anthropic.claude-3-haiku-20240307-v1:0": "2026-09-10"}
    assert all(index == expected for index in indexes)


@pytest.mark.parametrize(
    "model_id,aliases,expected",
    [
        ("gpt-4-1", ["gpt-4.1"], "GPT 4.1"),
        ("gpt-4-1-mini", ["gpt-4.1-mini"], "GPT 4.1 Mini"),
        ("gpt-4o-transcribe", None, "GPT 4o Transcribe"),
        ("gpt-o4-mini", None, "GPT o4 Mini"),
        ("deepseek-v3-2", ["deepseek-v3.2"], "DeepSeek V3.2"),
        ("claude-4-5-sonnet", ["claude-4.5"], "Claude 4.5 Sonnet"),
        ("mistral-large-latest", None, "Mistral Large Latest"),
        ("glm-5.2", None, "GLM 5.2"),
        ("glm-4-6", ["glm-4.6"], "GLM 4.6"),
        ("minimax-m3", None, "MiniMax M3"),
    ],
)
def test_to_display_name_keeps_vendor_casing(model_id, aliases, expected):
    assert public_api._to_display_name(model_id, aliases) == expected


@pytest.mark.parametrize(
    "model_id,litellm_provider,expected_name,expected_website",
    [
        ("glm-5.2", "deepinfra", "Z.ai", "https://z.ai"),
        ("glm-4-6", "openai", "Z.ai", "https://z.ai"),
        ("minimax-m3", "deepinfra", "MiniMax", "https://www.minimax.io"),
        # Existing vendors must keep resolving as before.
        ("deepseek-v4-flash", "deepinfra", "DeepSeek", "https://www.deepseek.com"),
        ("claude-4-5-sonnet", "bedrock", "Anthropic", "https://www.anthropic.com"),
    ],
)
def test_infer_manufacturer_covers_open_weight_vendors(
    model_id, litellm_provider, expected_name, expected_website
):
    item = {"model_info": {"litellm_provider": litellm_provider}}
    manufacturer = public_api._infer_manufacturer(model_id, item)
    assert manufacturer is not None, f"no manufacturer inferred for {model_id}"
    assert manufacturer.name == expected_name
    assert manufacturer.website == expected_website
