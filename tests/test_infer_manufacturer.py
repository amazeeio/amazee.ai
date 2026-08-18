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
