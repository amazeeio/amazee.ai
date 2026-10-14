"""DeepInfra's own model catalog: prices, limits and mode for its models.

First-party prices, so they beat LiteLLM's. Public, no key needed.
"""

from app.registry.litellm import fetch_json
from app.registry.values import per_unit

SOURCE = "deepinfra_api"
ORDER = 10
PRICE_ROLE = "override"
URL = "https://api.deepinfra.com/v1/openai/models"

PROVIDER = "deepinfra"
# DeepInfra field -> (LiteLLM field, units it is priced per). Tokens and
# characters are priced per million, seconds per second. `per_image_unit` is
# left out: it is not clear that one unit is one image.
_PRICES = {
    "input_tokens": ("input_cost_per_token", 1_000_000),
    "output_tokens": ("output_cost_per_token", 1_000_000),
    "cache_read_tokens": ("cache_read_input_token_cost", 1_000_000),
    "input_characters": ("input_cost_per_character", 1_000_000),
    "input_seconds": ("input_cost_per_second", 1),
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



def transform(payload: dict) -> dict:
    models = []
    for row in payload.get("data") or []:
        if not isinstance(row, dict) or not row.get("id"):
            continue
        meta = row.get("metadata") or {}
        pricing = meta.get("pricing") or {}
        base = {}
        for theirs, (ours, divisor) in _PRICES.items():
            value = per_unit(pricing.get(theirs), divisor)
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
