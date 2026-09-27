"""models.dev: fallback prices for models no other source prices.

models.dev agrees with LiteLLM on most prices but not all, so it is a fill
source: it never replaces a price from LiteLLM, a proxy or an override
plugin. Only models whose output is text are taken: its image and audio
prices do not fit per-token fields.
"""

from app.registry import config
from app.registry.litellm import parse_key
from app.registry.plugins.common import fetch_json, per_million

SOURCE = "models_dev"
ORDER = 100  # after first-party plugins, so it only fills what they left
PRICE_ROLE = "fill"
# Our models it may price: text modes, or no mode (proxy-only models).
FILL_MODES = {"chat", "completion", "responses"}

# models.dev provider -> ours. Bedrock prices are for Bedrock's own endpoint,
# so they are not used for bedrock_mantle.
PROVIDERS = {
    "amazon-bedrock": "bedrock",
    "google-vertex": "vertex_ai",
    "google-vertex-anthropic": "vertex_ai",
    "deepinfra": "deepinfra",
    "openai": "openai",
    "azure": "azure",
}
# models.dev field (USD per million tokens) -> LiteLLM field (USD per token).
FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write": "cache_creation_input_token_cost",
}
_SCOPE_NAMES = {"base": "base", "geo": "geo:{}", "cloud_region": "region:{}"}


def transform(data) -> dict:
    if not isinstance(data, dict):
        raise ValueError("models.dev payload is not an object")
    models: dict[tuple[str, str], dict] = {}
    for source_provider, provider in PROVIDERS.items():
        for key, model in ((data.get(source_provider) or {}).get("models") or {}).items():
            cost = model.get("cost") if isinstance(model, dict) else None
            parsed = parse_key(key, provider)
            if not isinstance(cost, dict) or parsed is None:
                continue
            # Image and audio models put non-token prices in these fields.
            if (model.get("modalities") or {}).get("output") not in (None, ["text"]):
                continue
            fields = {ours: per_million(cost.get(theirs)) for theirs, ours in FIELDS.items()}
            fields = {k: v for k, v in fields.items() if v is not None}
            if not fields:
                continue
            model_id, kind, scope = parsed
            # A geo id (`us.x`) is priced for that geo, never stored as base.
            entry = models.setdefault((provider, model_id), {"provider": provider, "model_id": model_id, "prices": {}})
            entry["prices"].setdefault(_SCOPE_NAMES[kind].format(scope), fields)
    if not models:
        raise ValueError("models.dev payload has no prices for our providers")
    return {"schema": 1, "source": SOURCE, "models": list(models.values())}


def parse() -> dict:
    return transform(fetch_json(config.MODELS_DEV_URL))
