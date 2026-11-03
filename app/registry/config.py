import os

from app.core.config import region_names, settings

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

# models.dev: a fallback price, only for models no other source prices.
MODELS_DEV_URL = os.getenv("REGISTRY_MODELS_DEV_URL", "https://models.dev/api.json")

# Source plugins to skip, by SOURCE name, comma separated: "deepinfra_api".
DISABLED_PLUGINS = {
    name.strip() for name in os.getenv("REGISTRY_DISABLED_PLUGINS", "").split(",") if name.strip()
}

HTTP_TIMEOUT_SECONDS = float(os.getenv("REGISTRY_HTTP_TIMEOUT_SECONDS", "30"))

# The list's `litellm_provider` is sometimes a sub-kind of the provider we
# deploy with. Any `vertex_ai-*` value is handled in code.
PROVIDER_ALIASES = {
    "bedrock_converse": "bedrock",
    "azure_text": "azure",
    "text-completion-openai": "openai",
}

BEDROCK_GEO_PREFIXES = {
    "us", "eu", "apac", "global", "jp", "au", "in", "ca", "sa", "kr", "il", "mx", "us-gov",
}

# A list that keeps fewer than this share of the models we already have is
# treated as a bad fetch, so one broken download cannot mark everything removed.
MIN_KEPT_RATIO = 0.5

# LiteLLM's list as a source of EOL dates. A plugin with a higher `eol_date`
# priority beats it.
LITELLM_EOL_PRIORITY = 50


def registry_manages(region_name: str) -> bool:
    """True if the registry sync may write to this region's LiteLLM proxy.

    Read at call time, so a test or an env-var change takes effect at once.
    The rule is the same as catalog_manages(): local manages every region.
    """
    if settings.ENV_SUFFIX == "local":
        return True
    return region_name in region_names(settings.REGISTRY_MANAGED_REGIONS)

# The env var that holds the Fernet keys for credential values, comma
# separated. The first key encrypts; every key decrypts, so a rotation can
# put a new key first and keep the old one until each value is written again.
CREDENTIAL_KEYS_ENV = "REGISTRY_CREDENTIAL_KEYS"

# How long a sync run started by a write waits for another run on the same proxy.
SYNC_LOCK_WAIT_SECONDS = float(os.getenv("REGISTRY_SYNC_LOCK_WAIT_SECONDS", "60"))
# The first key of the per-proxy advisory lock of the sync; the region id is the second.
# The old catalog's alias lock uses 743_291_648, so this one must differ.
SYNC_LOCK_NAMESPACE = 518_406_271
# A sync run that is pending or running for longer than this is marked failed by the daily step.
SYNC_STUCK_MINUTES = float(os.getenv("REGISTRY_SYNC_STUCK_MINUTES", "60"))
