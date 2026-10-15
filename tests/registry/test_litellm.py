import copy

import httpx
import pytest

from app.registry.litellm import (
    ProxyClient,
    mantle_region,
    normalize_provider,
    parse_key,
    registry_provider_and_model,
    split_model,
    without_alias_copies,
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
        ("openai.gpt-5.4", "bedrock_mantle", ("openai.gpt-5.4", False)),
        ("moonshotai.kimi-k2.5", "bedrock_mantle", ("moonshotai.kimi-k2.5", False)),
    ],
)
def test_split_model(key, provider, expected):
    assert split_model(key, provider) == expected


def test_normalize_provider():
    assert normalize_provider("vertex_ai-anthropic_models") == "vertex_ai"
    assert normalize_provider("bedrock_converse") == "bedrock"
    assert normalize_provider("bedrock_mantle") == "bedrock_mantle"
    assert normalize_provider(None) is None


MANTLE_US_EAST_1 = "https://bedrock-mantle.us-east-1.api.aws/openai/v1"
MANTLE_US_EAST_2 = "https://bedrock-mantle.us-east-2.api.aws/v1"


def test_registry_provider_and_model():
    assert registry_provider_and_model({"model": "bedrock/us.amazon.nova-micro-v1:0"}) == (
        "bedrock", "bedrock/us.amazon.nova-micro-v1:0"
    )
    assert registry_provider_and_model({"model": "nova", "custom_llm_provider": "bedrock"}) == ("bedrock", "nova")
    assert registry_provider_and_model({"model": "amazon.titan-embed-text-v2:0"}) == (
        None, "amazon.titan-embed-text-v2:0"
    )
    assert registry_provider_and_model(
        {"model": "amazon.titan-embed-text-v2:0"}, {"litellm_provider": "bedrock"}
    ) == ("bedrock", "amazon.titan-embed-text-v2:0")
    assert registry_provider_and_model({"model": "openai/openai.gpt-5.4", "api_base": MANTLE_US_EAST_1}) == (
        "bedrock_mantle", "openai.gpt-5.4"
    )
    assert registry_provider_and_model({"model": "openai/moonshotai.kimi-k2.5", "api_base": MANTLE_US_EAST_2}) == (
        "bedrock_mantle", "moonshotai.kimi-k2.5"
    )
    assert registry_provider_and_model({"model": "openai/gpt-4o"}) == ("openai", "openai/gpt-4o")
    assert registry_provider_and_model({"model": "openai/gpt-4o", "api_base": "https://api.openai.com/v1"}) == (
        "openai", "openai/gpt-4o"
    )
    assert registry_provider_and_model(
        {"model": "openai/openai.gpt-5.4", "api_base": "os.environ/MANTLE_API_BASE"}
    ) == ("openai", "openai/openai.gpt-5.4")
    assert registry_provider_and_model({"model": "azure/our-deployment"}, {"base_model": "azure/gpt-4o"}) == (
        "azure", "azure/gpt-4o"
    )


def test_mantle_region():
    assert mantle_region(MANTLE_US_EAST_1) == "us-east-1"
    assert mantle_region(MANTLE_US_EAST_2) == "us-east-2"
    assert mantle_region("https://api.openai.com/v1") is None
    assert mantle_region("https://bedrock-runtime.us-east-1.amazonaws.com") is None
    assert mantle_region("os.environ/X") is None
    assert mantle_region(None) is None


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
        "fallbacks": [{"api_key": "sk-live-1", "model": "x"}],
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
        "fallbacks": [{"model": "x"}],
    }
    assert data == before


def test_without_secrets_keeps_only_a_credential_name_string():
    assert without_secrets({"litellm_credential_name": {"api_key": "sk-live-1"}}) == {}


def test_without_secrets_cleans_urls():
    assert without_secrets({"api_base": "https://user:pass@host/v1?key=sk-live-1&api-version=2024"}) == {
        "api_base": "https://host/v1?api-version=2024"
    }
    assert without_secrets({"api_base": "https://host/v1"}) == {"api_base": "https://host/v1"}
    assert without_secrets({"api_base": "https://host/v1#access_token=sk-live-1"}) == {"api_base": "https://host/v1"}


def test_without_secrets_drops_signature_and_code_query_parameters():
    urls = without_secrets([
        "https://acct.blob.core.windows.net/c?sv=1&sig=S",
        "https://h/api?code=abc&x=1",
        "https://b.s3.amazonaws.com/o?X-Amz-Date=1&X-Amz-Signature=S",
    ])
    assert urls == [
        "https://acct.blob.core.windows.net/c?sv=1",
        "https://h/api?x=1",
        "https://b.s3.amazonaws.com/o?X-Amz-Date=1",
    ]
    # Outside a URL query, `code` is a normal config key.
    assert without_secrets({"code": "x"}) == {"code": "x"}


def test_without_secrets_reads_camel_case_and_joined_names():
    data = {
        "extra_headers": {
            "apiKey": "sk-live-1",
            "x-apikey": "sk-live-2",
            "accessToken": "sk-live-3",
            "clientSecret": "sk-live-4",
        }
    }
    assert without_secrets(data) == {"extra_headers": {}}


def _proxy(handler) -> ProxyClient:
    client = ProxyClient("http://proxy", "sk-test")
    client._client.close()
    client._client = httpx.Client(base_url="http://proxy", transport=httpx.MockTransport(handler))
    return client


def test_model_aliases_returns_the_alias_names():
    paths = []

    def handler(request):
        paths.append(request.url.path)
        return httpx.Response(200, json={"fields": [
            {"field_name": "routing_strategy", "field_value": "simple-shuffle"},
            {"field_name": "model_group_alias", "field_value": {
                "chat": "claude-x", "old": {"model": "claude-y", "hidden": True},
            }},
        ]})

    with _proxy(handler) as client:
        assert client.model_aliases() == {"chat", "old"}
    assert paths == ["/router/settings"]


@pytest.mark.parametrize(
    "fields",
    [
        [{"field_name": "routing_strategy", "field_value": "simple-shuffle"}],
        [{"field_name": "model_group_alias", "field_value": "chat"}],
    ],
)
def test_model_aliases_without_an_alias_map_is_empty(fields):
    with _proxy(lambda request: httpx.Response(200, json={"fields": fields})) as client:
        assert client.model_aliases() == set()


def test_model_aliases_raises_on_an_error_answer():
    with _proxy(lambda request: httpx.Response(500)) as client, pytest.raises(httpx.HTTPStatusError):
        client.model_aliases()


def _row(name, dep_id):
    return {"model_name": name, "model_info": {"id": dep_id}}


def test_without_alias_copies_drops_the_copy_of_a_real_row():
    real, copy_ = _row("claude-x", "1"), _row("chat", "1")

    assert without_alias_copies([copy_, real], {"chat"}) == ([real], ["chat"])


def test_without_alias_copies_keeps_a_real_row_whose_name_is_an_alias():
    rows = [_row("gpt-4o", "1"), _row("old-name", "2")]

    assert without_alias_copies(rows, {"old-name"}) == (rows, [])


def test_without_alias_copies_drops_a_self_alias_copy():
    # The alias has the name of its own target, so the first row is the real one.
    first, second = _row("claude-x", "1"), _row("claude-x", "1")

    assert without_alias_copies([first, second], {"claude-x"}) == ([first], ["claude-x"])
