"""The rules that a write of a model to a proxy must pass.

Kept apart from the API, so the planner can use them with no API import.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.registry.litellm import normalize_provider, parse_key
from app.registry.models import DBRegistryModel, DBRegistryModelSupport
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
    # `global` can send a call outside the region at every provider.
    if scope_kind == "geo" and prefix == "global":
        return "the geo prefix global is not allowed on this proxy"
    # Only a Bedrock geo prefix gets a region check. Another provider's geo form, such as an Azure data zone, passes.
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


def support_rows(db: Session, model_ids, region_ids) -> dict[tuple[int, int], DBRegistryModelSupport]:
    """The support rows of the given models on the given proxies, by (model_id, region_id), in one query."""
    if not model_ids or not region_ids:
        return {}
    rows = db.query(DBRegistryModelSupport).filter(
        DBRegistryModelSupport.model_id.in_(model_ids), DBRegistryModelSupport.region_id.in_(region_ids)
    )
    return {(row.model_id, row.region_id): row for row in rows}


def enable_problems(
    model: DBRegistryModel,
    provider: str,
    support: DBRegistryModelSupport | None,
    availability: Availability,
    cloud_region: str | None,
    backend: str,
) -> list[str]:
    """Rule 4: why the model cannot be turned on with this backend on the proxy. Reads only the given data."""
    problems = []
    if support is None or support.supported is None:
        problems.append("support is unknown: the support check has no result for this proxy")
    elif support.supported is False:
        problems.append("the proxy's LiteLLM version does not support the model")
    if support is not None and support.region_available is False:
        problems.append("the proxy's cloud region does not offer the model")
    # LiteLLM fails both at call time. The default backend never gives `needs_profile`,
    # so it refuses only an in-region id that a request gives on purpose.
    in_region = availability.in_region_problem(provider, model.model_id, cloud_region, backend)
    if in_region == "provisioned_only":
        problems.append("the proxy's cloud region offers the model only with provisioned throughput")
    elif in_region == "needs_profile":
        geos = geo_prefixes(cloud_region)
        hint = f"; use the {geos[0]}. prefix" if geos else ""
        problems.append(f"the proxy's cloud region offers the model only through a geo profile{hint}")
    if model.blocked:
        problems.append("the model is blocked")
    return problems


def removal_problem(model: DBRegistryModel, today: date) -> str | None:
    """Rule 1: a model leaves a proxy only on or after its EOL date. `eol_date` already holds the override."""
    if model.eol_date is not None and model.eol_date > today:
        return f"the model reaches its EOL date on {model.eol_date}; use force to remove it before that date"
    return None
