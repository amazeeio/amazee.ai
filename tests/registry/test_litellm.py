import pytest

from app.registry.litellm import deployment_provider, normalize_provider, split_model


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
