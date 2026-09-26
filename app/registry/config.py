import os

# The same list our proxies download from `main` when they start, so the
# registry knows a model as soon as a restarted proxy can price it.
LITELLM_LIST_URL = os.getenv(
    "REGISTRY_LITELLM_LIST_URL",
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
)

HTTP_TIMEOUT_SECONDS = float(os.getenv("REGISTRY_HTTP_TIMEOUT_SECONDS", "30"))

# The list's `litellm_provider` is sometimes a sub-kind of the provider we
# deploy with. Any `vertex_ai-*` value is handled in code.
PROVIDER_ALIASES = {
    "bedrock_converse": "bedrock",
    "azure_text": "azure",
    "text-completion-openai": "openai",
}

BEDROCK_GEO_PREFIXES = {
    "us", "eu", "apac", "global", "jp", "au", "ca", "sa", "kr", "il", "mx", "us-gov",
}

# A list that keeps fewer than this share of the models we already have is
# treated as a bad fetch, so one broken download cannot deprecate everything.
MIN_KEPT_RATIO = 0.5
