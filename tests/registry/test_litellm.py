import pytest

from app.registry.litellm import deployment_provider, normalize_provider, parse_key, split_model


@pytest.mark.parametrize(
    "key, provider, expected",
    [
        ("anthropic.claude-3-haiku-20240307-v1:0", "bedrock", ("anthropic.claude-3-haiku-20240307-v1:0", False)),
        ("bedrock/us.anthropic.claude-3-haiku-20240307-v1:0", "bedrock", ("anthropic.claude-3-haiku-20240307-v1:0", True)),
        ("us-gov.anthropic.claude-opus-5", "bedrock", ("anthropic.claude-opus-5", True)),
        ("bedrock/converse/amazon.nova-lite-v1:0", "bedrock", ("amazon.nova-lite-v1:0", False)),
        ("bedrock/us-east-1/anthropic.claude-v2:1", "bedrock", None),
        ("1024-x-1024/50-steps/bedrock/amazon.nova-canvas-v1:0", "bedrock", None),
        ("deepinfra/meta-llama/Meta-Llama-3.1-8B-Instruct", "deepinfra", ("meta-llama/Meta-Llama-3.1-8B-Instruct", False)),
        ("vertex_ai/claude-haiku-4-5", "vertex_ai", ("claude-haiku-4-5", False)),
        ("gpt-4o", "openai", ("gpt-4o", False)),
    ],
)
def test_split_model(key, provider, expected):
    assert split_model(key, provider) == expected


def test_normalize_provider():
    assert normalize_provider("vertex_ai-anthropic_models") == "vertex_ai"
    assert normalize_provider("bedrock_converse") == "bedrock"
    assert normalize_provider("bedrock_mantle") == "bedrock_mantle"
    assert normalize_provider(None) is None


def test_deployment_provider():
    assert deployment_provider({"model": "bedrock/us.amazon.nova-micro-v1:0"}) == "bedrock"
    assert deployment_provider({"model": "nova", "custom_llm_provider": "bedrock"}) == "bedrock"
    assert deployment_provider({"model": "amazon.titan-embed-text-v2:0"}) is None


@pytest.mark.parametrize(
    "key, provider, expected",
    [
        ("anthropic.claude-x-v1:0", "bedrock", ("anthropic.claude-x-v1:0", "base", "")),
        ("us.anthropic.claude-x-v1:0", "bedrock", ("anthropic.claude-x-v1:0", "geo", "us")),
        ("bedrock/us-gov-west-1/anthropic.claude-x-v1:0", "bedrock", ("anthropic.claude-x-v1:0", "cloud_region", "us-gov-west-1")),
        ("bedrock/ap-northeast-1/1-month-commitment/anthropic.claude-v2:1", "bedrock", None),
        ("azure/eu/gpt-4o", "azure", ("gpt-4o", "geo", "eu")),
        ("azure/gpt-4o", "azure", ("gpt-4o", "base", "")),
        ("deepinfra/meta-llama/Llama-3", "deepinfra", ("meta-llama/Llama-3", "base", "")),
        ("bedrock_converse/us.anthropic.claude-x-v1:0", "bedrock", ("anthropic.claude-x-v1:0", "geo", "us")),
        ("azure_text/eu/gpt-35-turbo-instruct", "azure", ("gpt-35-turbo-instruct", "geo", "eu")),
        ("text-completion-openai/gpt-3.5-turbo-instruct", "openai", ("gpt-3.5-turbo-instruct", "base", "")),
    ],
)
def test_parse_key(key, provider, expected):
    assert parse_key(key, provider) == expected


def test_split_model_skips_region_prices_and_azure_zones_name_the_model():
    assert split_model("bedrock/us-east-1/anthropic.claude-x-v1:0", "bedrock") is None
    assert split_model("azure/eu/gpt-4o", "azure") == ("gpt-4o", True)
