import os

# The same list our proxies download from `main` when they start, so the
# registry knows a model as soon as a restarted proxy can price it.
LITELLM_LIST_URL = os.getenv(
    "REGISTRY_LITELLM_LIST_URL",
    "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json",
)

# The list as shipped with one release. A proxy that runs that release but
# loaded `main` can price newer models its code was never tested with.
RELEASE_LIST_URL = os.getenv(
    "REGISTRY_LITELLM_RELEASE_LIST_URL",
    "https://raw.githubusercontent.com/BerriAI/litellm/v{version}/model_prices_and_context_window.json",
)

# A community mirror of AWS's Bedrock model list: regions, call types and
# lifecycle dates per model. Our own setting, not the old catalog's.
BEDROCK_CATALOG_URL = os.getenv(
    "REGISTRY_BEDROCK_CATALOG_URL",
    "https://raw.githubusercontent.com/amazonbedrockmodels/amazonbedrockmodels.github.io/main/data/models.json",
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
# treated as a bad fetch, so one broken download cannot mark everything removed.
MIN_KEPT_RATIO = 0.5
