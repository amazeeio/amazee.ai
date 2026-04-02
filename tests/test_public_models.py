import httpx
from unittest.mock import AsyncMock, patch

from app.api import public as public_api
from app.db.models import DBRegion


def _clear_public_models_cache():
    public_api._models_cache["data"] = []
    public_api._models_cache["expires_at"] = public_api.datetime.min.replace(
        tzinfo=public_api.UTC
    )


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
