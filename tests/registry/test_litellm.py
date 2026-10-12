import copy

import pytest

from app.registry.litellm import (
    deployment_provider,
    normalize_provider,
    parse_key,
    split_model,
    without_secrets,
)


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
    assert (
        deployment_provider({"model": "amazon.titan-embed-text-v2:0"}, {"litellm_provider": "bedrock"})
        == "bedrock"
    )


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
        ("in.moonshotai.kimi-k3", "bedrock", ("moonshotai.kimi-k3", "geo", "in")),
    ],
)
def test_parse_key(key, provider, expected):
    assert parse_key(key, provider) == expected


def test_split_model_skips_region_prices_and_azure_zones_name_the_model():
    assert split_model("bedrock/us-east-1/anthropic.claude-x-v1:0", "bedrock") is None
    assert split_model("azure/eu/gpt-4o", "azure") == ("gpt-4o", True)


def test_without_secrets():
    data = {
        "model": "bedrock/us.anthropic.claude-x-v1:0",
        "api_key": "sk-live-1234",
        "aws_access_key_id": "os.environ/AWS_ACCESS_KEY_ID",
        "aws_secret_access_key": "abcd****wxyz",
        "vertex_credentials": {"type": "service_account", "private_key": "x"},
        "litellm_credential_name": "AWS Bedrock",
        "api_base": "https://example.com",
        "aws_region_name": "eu-central-1",
        "vertex_project": "proj",
        "input_cost_per_token": 1e-06,
        "max_tokens": 4096,
        "additional_drop_params": ["temperature", "top_p"],
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_headers": {"Authorization": "Bearer abc", "X-Tenant": "amazee"},
    }
    before = copy.deepcopy(data)

    assert without_secrets(data) == {
        "model": "bedrock/us.anthropic.claude-x-v1:0",
        "aws_access_key_id": "os.environ/AWS_ACCESS_KEY_ID",
        "litellm_credential_name": "AWS Bedrock",
        "api_base": "https://example.com",
        "aws_region_name": "eu-central-1",
        "vertex_project": "proj",
        "input_cost_per_token": 1e-06,
        "max_tokens": 4096,
        "additional_drop_params": ["temperature", "top_p"],
        "chat_template_kwargs": {"enable_thinking": False},
        "extra_headers": {"X-Tenant": "amazee"},
    }
    assert data == before


def test_without_secrets_keeps_only_a_credential_name_string():
    assert without_secrets({"litellm_credential_name": {"api_key": "sk-live-1"}}) == {}


def test_without_secrets_cleans_urls():
    assert without_secrets({"api_base": "https://user:pass@host/v1?key=sk-live-1&api-version=2024"}) == {
        "api_base": "https://host/v1?api-version=2024"
    }
    assert without_secrets({"api_base": "https://host/v1"}) == {"api_base": "https://host/v1"}


def test_without_secrets_reads_camel_case_and_joined_names():
    assert without_secrets({"extra_headers": {"apiKey": "sk-live-1", "x-apikey": "sk-live-2"}}) == {
        "extra_headers": {}
    }
