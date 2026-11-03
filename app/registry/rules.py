"""The rules that a write of a model to a proxy must pass.

Kept apart from the API, so the planner can use them with no API import.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.registry.litellm import normalize_provider, parse_key
from app.registry.models import (
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelSupport,
    DBRegistryProvider,
)
from app.registry.prices import pick_price
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
# Rule 6: providers that use the same credentials. Bedrock Mantle takes the AWS keys of Bedrock.
CREDENTIAL_PROVIDERS = {"bedrock_mantle": "bedrock"}


def credential_provider(provider: str) -> str:
    """The provider whose credentials `provider` uses. Rule 6 compares only these names."""
    return CREDENTIAL_PROVIDERS.get(provider, provider)


def user_providers(db: Session, names) -> dict[str, set[str]]:
    """The mapped providers of the deployments, on any proxy, that use each credential name.

    A credential from the adopt import has no provider, so rule 6 takes these instead.
    """
    used_by = DBRegistryModelRegion.litellm_params["litellm_credential_name"].as_string()
    found: dict[str, set[str]] = {}
    if names:
        for name, provider in (
            db.query(used_by, DBRegistryProvider.name)
            .join(DBRegistryModel, DBRegistryModel.id == DBRegistryModelRegion.model_id)
            .join(DBRegistryProvider, DBRegistryProvider.id == DBRegistryModel.provider_id)
            .filter(used_by.in_(list(names)))
            .distinct()
        ):
            found.setdefault(name, set()).add(credential_provider(provider))
    return found


# `{proxy}` and `{key}` are the literal path text: the rule functions do not know the names.
UNKNOWN_BEDROCK_REGION = "the proxy's Bedrock region is unknown; set it with PATCH /registry/proxies/{proxy}"
NO_PRICE = "the model has no price on the proxy or in the registry; set one with PATCH /registry/models/{key}"


def geo_prefixes(cloud_region: str | None) -> tuple[str, ...]:
    """The Bedrock geo prefixes allowed for an AWS region; the first one is the default.

    `global` is never allowed, because it can send a call outside the region.
    """
    if cloud_region in _GEOS_BY_REGION:
        return _GEOS_BY_REGION[cloud_region]
    region = cloud_region or ""
    return next((geos for family, geos in _GEOS_BY_FAMILY if region.startswith(family)), ())


def backend_problem(backend: str, provider: str, model_id: str, cloud_regions: dict) -> str | None:
    """Rule 5: why a backend string cannot be deployed for this model on this proxy, or None.

    A Bedrock backend in the cloud-region form must name the proxy's Bedrock region.
    """
    head, sep, _ = backend.partition("/")
    if not sep or normalize_provider(head) != provider:
        return "the backend is for another model"
    parsed = parse_key(backend, provider)
    if parsed is None or (parsed[1] == "cloud_region" and provider != "bedrock"):
        return "the backend is not a model string the registry deploys"
    if parsed[0] != model_id:
        return "the backend is for another model"
    return geo_problem(backend, provider, cloud_regions)


def geo_problem(backend: str, provider: str, cloud_regions: dict) -> str | None:
    """The region part of rule 5 only.

    A stored backend gets only this part: the import found its model from the full litellm_params, and a
    Bedrock Mantle deployment is stored as `openai/...`. A Bedrock geo or cloud-region form needs a known region.
    """
    parsed = parse_key(backend, provider)
    if parsed is None:
        return None
    _, kind, scope = parsed
    # `global` can send a call outside the region at every provider.
    if kind == "geo" and scope == "global":
        return "the geo prefix global is not allowed on this proxy"
    # Only Bedrock gets a region check. Another provider's geo form, such as an Azure data zone, passes.
    if provider != "bedrock" or kind == "base":
        return None
    region = cloud_regions.get("bedrock")
    if not region:
        return UNKNOWN_BEDROCK_REGION
    if kind == "geo" and scope not in geo_prefixes(region):
        return f"the geo prefix {scope} is not allowed on this proxy"
    if kind == "cloud_region" and scope != region:
        return f"the backend names the cloud region {scope}, not the proxy's Bedrock region {region}"
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
    rows = db.query(DBRegistryModelSupport).filter(
        DBRegistryModelSupport.model_id.in_(model_ids), DBRegistryModelSupport.region_id.in_(region_ids)
    )
    return {(row.model_id, row.region_id): row for row in rows}


def _call_type_problem(
    provider: str, model_id: str, availability: Availability, cloud_region: str | None, backend: str
) -> str | None:
    """Why the proxy's cloud region cannot call the model in this backend form, or None.

    LiteLLM fails these at call time. With no call types for the region, support and region availability decide.
    """
    provisioned = "the proxy's cloud region offers the model only with provisioned throughput"
    parsed = parse_key(backend, provider)
    call_types = availability.call_types.get((provider, model_id, cloud_region))
    if provider == "bedrock" and parsed and parsed[1] == "geo" and call_types:
        if "INFERENCE_PROFILE" in call_types:
            return None
        if set(call_types) == {"PROVISIONED"}:
            return provisioned
        return "the proxy's cloud region does not offer the model through a geo profile"
    # The default backend never gives `needs_profile`, so it refuses only an in-region id that a request gives.
    in_region = availability.in_region_problem(provider, model_id, cloud_region, backend)
    if in_region == "provisioned_only":
        return provisioned
    if in_region == "needs_profile":
        geos = geo_prefixes(cloud_region)
        hint = f"; use the {geos[0]}. prefix" if geos else ""
        return f"the proxy's cloud region offers the model only through a geo profile{hint}"
    return None


def enable_problems(
    model: DBRegistryModel,
    provider: str,
    support: DBRegistryModelSupport | None,
    availability: Availability,
    cloud_region: str | None,
    backend: str,
    prices: list[DBRegistryModelPrice],
    own_prices: dict | None = None,
) -> list[str]:
    """Rule 4: why the model cannot be turned on with this backend on the proxy. Reads only the given data.

    `prices` are the model's registry price rows, `own_prices` the prices stored on the deployment.
    """
    problems = []
    if support is None or support.supported is None:
        problems.append("support is unknown: the support check has no result for this proxy")
    elif support.supported is False:
        problems.append("the proxy's LiteLLM version does not support the model")
    if support is not None and support.region_available is False:
        problems.append("the proxy's cloud region does not offer the model")
    if problem := _call_type_problem(provider, model.model_id, availability, cloud_region, backend):
        problems.append(problem)
    if model.blocked:
        problems.append("the model is blocked")
    # With no price anywhere, LiteLLM logs the spend as $0, so budgets miss it.
    # ponytail: `priced` is per model, not per scope, so a proxy price for another geo passes; the daily
    # `deployed_unpriced` stat still lists it. Store the proxy's scopes if that gap matters.
    if not own_prices and not (support and support.priced) and pick_price(prices, provider, backend, cloud_region) is None:
        problems.append(NO_PRICE)
    return problems


def removal_problem(model: DBRegistryModel, today: date) -> str | None:
    """Rule 1: a model leaves a proxy only on or after its EOL date. `eol_date` already holds the override."""
    if model.eol_date is not None and model.eol_date > today:
        return f"the model reaches its EOL date on {model.eol_date}; use force to remove it before that date"
    return None
