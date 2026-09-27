"""DeepInfra's own model catalog: prices, limits and mode for its models.

First-party prices, so they beat LiteLLM's. Public, no key needed.
"""

from app.registry.plugins.common import fetch_json, per_million

SOURCE = "deepinfra_api"
ORDER = 10
PRICE_ROLE = "override"
URL = "https://api.deepinfra.com/v1/openai/models"

PROVIDER = "deepinfra"
# DeepInfra field -> (LiteLLM field, divisor). Tokens and characters are
# priced per million, seconds per second. `per_image_unit` is left out: it is
# not clear that one unit is one image.
_PRICES = {
    "input_tokens": ("input_cost_per_token", True),
    "output_tokens": ("output_cost_per_token", True),
    "cache_read_tokens": ("cache_read_input_token_cost", True),
    "input_characters": ("input_cost_per_character", True),
    "input_seconds": ("input_cost_per_second", False),
}
# The first matching tag wins.
_MODES = (
    ("embed", "embedding"),
    ("stt", "audio_transcription"),
    ("tts", "audio_speech"),
    ("image-gen", "image_generation"),
    ("video-gen", "video_generation"),
    ("chat", "chat"),
)


def _price(value, per_million_units: bool):
    if per_million_units:
        return per_million(value)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
        return None
    return float(value)


def transform(payload: dict) -> dict:
    models = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        meta = row.get("metadata") or {}
        pricing = meta.get("pricing") or {}
        base = {}
        for theirs, (ours, is_per_million) in _PRICES.items():
            value = _price(pricing.get(theirs), is_per_million)
            if value is not None:
                base[ours] = value
        tags = set(meta.get("tags") or [])
        models.append(
            {
                "provider": PROVIDER,
                "model_id": row["id"],
                "mode": next((mode for tag, mode in _MODES if tag in tags), None),
                "max_input_tokens": meta.get("context_length") if isinstance(meta.get("context_length"), int) else None,
                "max_output_tokens": meta.get("max_tokens") if isinstance(meta.get("max_tokens"), int) else None,
                "prices": {"base": base} if base else {},
            }
        )
    return {"schema": 1, "source": SOURCE, "models": models}


def parse() -> dict:
    return transform(fetch_json(URL))
