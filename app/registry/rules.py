"""The rules that a write of a model to a proxy must pass.

Kept apart from the API, so the planner can use them with no API import.
"""

from app.registry.litellm import normalize_provider, parse_key
from app.registry.support import Availability

_GEOS_BY_REGION = {
    "ap-southeast-2": ("au", "apac"),
    "ap-southeast-4": ("au", "apac"),
    "ap-northeast-1": ("jp", "apac"),
}
# Checked in order: `us-gov-` must come before `us-`.
_GEOS_BY_FAMILY = (
    ("us-gov-", ("us-gov",)),
    ("us-", ("us",)),
    ("eu-", ("eu",)),
    ("ap-", ("apac",)),
    ("ca-", ("ca",)),
    ("sa-", ("sa",)),
    ("il-", ("il",)),
    ("mx-", ("mx",)),
)


def geo_prefixes(cloud_region: str | None) -> tuple[str, ...]:
    """The Bedrock geo prefixes allowed for an AWS region; the first one is the default.

    `global` is never allowed, because it can send a call outside the region.
    """
    if cloud_region in _GEOS_BY_REGION:
        return _GEOS_BY_REGION[cloud_region]
    region = cloud_region or ""
    return next((geos for family, geos in _GEOS_BY_FAMILY if region.startswith(family)), ())


def backend_problem(backend: str, provider: str, model_id: str, cloud_regions: dict) -> str | None:
    """Rule 5: why a backend string cannot be deployed for this model on this proxy, or None."""
    head, sep, _ = backend.partition("/")
    if not sep or normalize_provider(head) != provider:
        return "the backend is for another model"
    parsed = parse_key(backend, provider)
    if parsed is None or parsed[1] == "cloud_region":
        return "the backend is not a model string the registry deploys"
    parsed_id, scope_kind, prefix = parsed
    if parsed_id != model_id:
        return "the backend is for another model"
    # Other providers get a geo check when their region data exists; the map below is for Bedrock only.
    if provider == "bedrock" and scope_kind == "geo" and prefix not in geo_prefixes(cloud_regions.get("bedrock")):
        return f"the geo prefix {prefix} is not allowed on this proxy"
    return None


def default_backend(provider: str, model_id: str, cloud_regions: dict, availability: Availability) -> str:
    """The backend string for a new deployment: the geo profile id when the
    proxy's Bedrock region offers the model through one, else the in-region id."""
    if provider == "bedrock":
        region = cloud_regions.get("bedrock")
        geos = geo_prefixes(region)
        if geos and "INFERENCE_PROFILE" in (availability.call_types.get(("bedrock", model_id, region)) or []):
            return f"bedrock/{geos[0]}.{model_id}"
    return f"{provider}/{model_id}"
