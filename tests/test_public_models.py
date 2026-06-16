import pytest
import httpx
from unittest.mock import AsyncMock, patch

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


def test_prompt_caching_enabled_when_supports_and_costs_present(client, db):
    """prompt_caching_enabled is True only when supports_prompt_caching=True AND cache costs are set."""
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
        mock_service.get_cost_margin_config = AsyncMock(return_value={"values": {"global": 0.0}})

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "caching-enabled-region")
        model = region_data["models"][0]
        assert model["capabilities"]["supports_prompt_caching"] is True
        assert model["capabilities"]["prompt_caching_enabled"] is True
        assert model["pricing"]["cache_creation_input_cost_per_million_tokens"] == pytest.approx(3.0)
        assert model["pricing"]["cache_creation_input_cost_above_1hr_per_million_tokens"] == pytest.approx(6.0)
        assert model["pricing"]["cache_read_input_cost_per_million_tokens"] == pytest.approx(0.3)


def test_prompt_caching_enabled_matches_supports_prompt_caching(client, db):
    """prompt_caching_enabled mirrors supports_prompt_caching as the best available signal."""
    _clear_public_models_cache()
    region = DBRegion(
        name="caching-no-cost-region",
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
                        "model_name": "claude-3-haiku",
                        "litellm_params": {},
                        "model_info": {
                            "mode": "chat",
                            "supports_prompt_caching": True,
                            # No cache cost fields
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(return_value={"values": {"global": 0.0}})

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "caching-no-cost-region")
        model = region_data["models"][0]
        # prompt_caching_enabled follows supports_prompt_caching
        assert model["capabilities"]["supports_prompt_caching"] is True
        assert model["capabilities"]["prompt_caching_enabled"] is True
        assert model["pricing"]["cache_creation_input_cost_per_million_tokens"] is None
        assert model["pricing"]["cache_creation_input_cost_above_1hr_per_million_tokens"] is None
        assert model["pricing"]["cache_read_input_cost_per_million_tokens"] is None


def test_prompt_caching_enabled_false_when_supports_false(client, db):
    """prompt_caching_enabled is False when supports_prompt_caching is False/null, cache pricing nulled."""
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
                            # LiteLLM static DB might still return cache costs — should be nulled
                            "cache_creation_input_token_cost": 0.000003,
                            "cache_read_input_token_cost": 0.0000003,
                        },
                    }
                ]
            }
        )
        mock_service.get_cost_margin_config = AsyncMock(return_value={"values": {"global": 0.0}})

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        region_data = next(r for r in data if r["region"] == "caching-unsupported-region")
        model = region_data["models"][0]
        assert model["capabilities"]["supports_prompt_caching"] is False
        assert model["capabilities"]["prompt_caching_enabled"] is False
        assert model["pricing"]["cache_creation_input_cost_per_million_tokens"] is None
        assert model["pricing"]["cache_creation_input_cost_above_1hr_per_million_tokens"] is None
        assert model["pricing"]["cache_read_input_cost_per_million_tokens"] is None


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
