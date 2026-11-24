"""Unit tests for _infer_manufacturer covering keyword and provider-based matching."""

import pytest

from app.api.public import _infer_manufacturer


def _item(litellm_provider: str = "") -> dict:
    return {"model_info": {"litellm_provider": litellm_provider}}


class TestKeywordMatch:
    """Models whose IDs contain a known keyword."""

    @pytest.mark.parametrize(
        "model_id,expected_name",
        [
            ("gpt-4o", "OpenAI"),
            ("claude-3-5-sonnet-20241022", "Anthropic"),
            ("gemini-1.5-pro", "Google"),
            ("gemma-2-27b", "Google"),
            ("llama-3.1-70b", "Meta"),
            ("mistral-large-latest", "Mistral AI"),
            ("pixtral-12b-2409", "Mistral AI"),
            ("mixtral-8x7b", "Mistral AI"),
            ("ministral-8b", "Mistral AI"),
            ("devstral-small", "Mistral AI"),
            ("magistral-medium", "Mistral AI"),
            ("deepseek-v3", "DeepSeek"),
            ("kimi-k2", "Moonshot"),
            ("qwen-2.5-72b", "Alibaba"),
            ("titan-text-express", "Amazon"),
        ],
    )
    def test_keyword_in_model_id(self, model_id: str, expected_name: str):
        result = _infer_manufacturer(model_id, _item())
        assert result is not None
        assert result.name == expected_name


class TestProviderFallback:
    """Models whose IDs lack a keyword but provider string matches."""

    @pytest.mark.parametrize(
        "model_id,provider,expected_name",
        [
            ("o1", "openai", "OpenAI"),
            ("o3-mini", "openai", "OpenAI"),
            ("o4-mini", "openai", "OpenAI"),
            ("dall-e-3", "openai", "OpenAI"),
            ("text-embedding-3-large", "openai", "OpenAI"),
            ("some-custom-model", "anthropic", "Anthropic"),
            ("palm-2", "google_ai_studio", "Google"),
            ("custom-llm", "meta", "Meta"),
        ],
    )
    def test_provider_based_match(
        self, model_id: str, provider: str, expected_name: str
    ):
        result = _infer_manufacturer(model_id, _item(provider))
        assert result is not None
        assert result.name == expected_name


class TestUnknownReturnsNone:
    """Models with no matching keyword or provider return None."""

    @pytest.mark.parametrize(
        "model_id,provider",
        [
            ("my-custom-model", ""),
            ("some-finetune", "custom_provider"),
            ("random-model", "bedrock_converse"),
        ],
    )
    def test_returns_none(self, model_id: str, provider: str):
        result = _infer_manufacturer(model_id, _item(provider))
        assert result is None


class TestKeywordPriority:
    """Keyword in model ID takes priority over provider fallback."""

    def test_model_keyword_wins_over_provider(self):
        # Model ID says "deepseek" but provider says "openai"
        result = _infer_manufacturer("deepseek-v3", _item("openai"))
        assert result is not None
        assert result.name == "DeepSeek"


class TestLogoUrl:
    """Every keyword rule except Meta has a logo, and it is served without auth."""

    @pytest.mark.parametrize(
        "model_id,slug",
        [
            ("gpt-4o", "openai"),
            ("claude-sonnet-4", "anthropic"),
            ("gemini-2.5-pro", "google"),
            ("mistral-large-latest", "mistralai"),
            ("deepseek-v3", "deepseek"),
            ("kimi-k2", "moonshot"),
            ("glm-4.6", "zai"),
            ("minimax-m2", "minimax"),
            ("qwen-2.5-72b", "alibaba"),
            ("titan-text-express", "amazon"),
        ],
    )
    def test_logo_url_is_served(self, client, model_id: str, slug: str):
        url = _infer_manufacturer(model_id, _item()).logo_url
        assert url.endswith(f"/logos/{slug}.png")
        response = client.get(f"/logos/{slug}.png")
        assert response.status_code == 200
        assert response.headers["content-type"] == "image/png"

    def test_no_logo_is_none(self):
        assert _infer_manufacturer("llama-3.1-70b", _item()).logo_url is None
