from unittest.mock import AsyncMock, patch

from app.api import public as public_api


def _clear_public_models_cache():
    public_api._models_cache["data"] = []
    public_api._models_cache["expires_at"] = public_api.datetime.min.replace(
        tzinfo=public_api.UTC
    )


def test_public_models_returns_aggregated_data(client, db):
    _clear_public_models_cache()
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
        first = data[0]
        assert first["model_id"] == "claude-3-5-sonnet-20241022"
        assert first["provider"] == "aws"
        assert first["region"] == "eu-central-1"
        assert first["type"] == "chat"
        assert first["context_length"] == 200000
        assert first["status"] == "ga"


def test_public_models_includes_unavailable_region(client, db):
    _clear_public_models_cache()
    with patch("app.api.public.LiteLLMService") as mock_service_cls:
        mock_service = mock_service_cls.return_value
        mock_service.get_model_info = AsyncMock(side_effect=Exception("timeout"))

        response = client.get("/public/models")
        assert response.status_code == 200
        data = response.json()
        assert len(data) >= 1
        assert any(item["status"] == "unavailable" for item in data)
