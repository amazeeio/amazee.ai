"""Read-only access to LiteLLM: a proxy's admin API and the public model list."""

import re

import httpx

from app.registry.config import BEDROCK_GEO_PREFIXES, HTTP_TIMEOUT_SECONDS, PROVIDER_ALIASES

# Bedrock route prefixes that pick an API, not a different model.
_BEDROCK_ROUTES = ("converse/", "invoke/")
_AWS_REGION_PATH = re.compile(r"^([a-z]{2}(?:-gov)?-[a-z]+-\d+)/([^/]+)$")
# Azure data zones: `azure/eu/gpt-4o` is gpt-4o priced for the EU zone.
AZURE_DATA_ZONES = {"us", "eu", "global"}


def normalize_provider(name: str | None) -> str | None:
    if not name:
        return None
    name = name.lower()
    if name.startswith("vertex_ai"):
        return "vertex_ai"
    return PROVIDER_ALIASES.get(name, name)


def parse_key(key: str, provider: str) -> tuple[str, str, str] | None:
    """Turn a LiteLLM model string into (model_id, scope_kind, scope).

    scope_kind is `base`, `geo` (Bedrock `us.x`, Azure `azure/eu/x`) or
    `cloud_region` (Bedrock `bedrock/us-east-1/x`). Returns None for Bedrock
    entries priced per commitment or image size, which we do not track.
    """
    rest = key.removeprefix(f"{provider}/")
    if provider == "azure":
        zone, sep, tail = rest.partition("/")
        if sep and zone in AZURE_DATA_ZONES:
            return tail, "geo", zone
        return rest, "base", ""
    if not provider.startswith("bedrock"):
        return rest, "base", ""
    for route in _BEDROCK_ROUTES:
        rest = rest.removeprefix(route)
    region = _AWS_REGION_PATH.match(rest)
    if region:
        return region[2], "cloud_region", region[1]
    if "/" in rest:
        return None
    head, sep, tail = rest.partition(".")
    if sep and tail and head in BEDROCK_GEO_PREFIXES:
        return tail, "geo", head
    return rest, "base", ""


def split_model(key: str, provider: str) -> tuple[str, bool] | None:
    """(model_id, had_geo_prefix) for a string that names a model.

    None for entries that only price a model in one AWS region, per
    commitment or per image size.
    """
    parsed = parse_key(key, provider)
    if parsed is None or parsed[1] == "cloud_region":
        return None
    return parsed[0], parsed[1] == "geo"


def our_entries(data: dict, providers: set[str]):
    """(key, provider, entry) for each list entry of one of our providers."""
    for key, entry in data.items():
        if not isinstance(entry, dict) or key == "sample_spec":
            continue
        provider = normalize_provider(entry.get("litellm_provider"))
        if provider in providers:
            yield key, provider, entry


def deployment_provider(litellm_params: dict) -> str | None:
    """The provider a deployment calls, as LiteLLM resolves it."""
    model = litellm_params.get("model") or ""
    explicit = litellm_params.get("custom_llm_provider")
    if explicit:
        return normalize_provider(explicit)
    if "/" in model:
        return normalize_provider(model.split("/", 1)[0])
    return None


def fetch_json(url: str):
    response = httpx.get(url, timeout=HTTP_TIMEOUT_SECONDS)
    response.raise_for_status()
    return response.json()


class ProxyClient:
    def __init__(self, url: str, master_key: str):
        self._client = httpx.Client(
            base_url=url.rstrip("/"),
            headers={"Authorization": f"Bearer {master_key}"},
            timeout=HTTP_TIMEOUT_SECONDS,
        )

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self._client.close()

    def _get(self, path: str):
        response = self._client.get(path)
        response.raise_for_status()
        return response.json()

    def model_info(self) -> list[dict]:
        return self._get("/model/info").get("data") or []

    def version(self) -> str | None:
        try:
            return self._get("/openapi.json").get("info", {}).get("version")
        except httpx.HTTPError:
            return None

    def credential_regions(self) -> dict[str, str]:
        """Map credential name to its `aws_region_name`. Reads nothing else."""
        try:
            data = self._get("/credentials")
        except httpx.HTTPError:
            return {}
        regions = {}
        for cred in data.get("credentials") or []:
            region = (cred.get("credential_info") or {}).get("aws_region_name")
            if cred.get("credential_name") and region:
                regions[cred["credential_name"]] = region
        return regions
