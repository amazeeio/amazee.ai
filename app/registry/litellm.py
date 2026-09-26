"""Read-only access to LiteLLM: a proxy's admin API and the public model list."""

import httpx

from app.registry.config import BEDROCK_GEO_PREFIXES, HTTP_TIMEOUT_SECONDS, PROVIDER_ALIASES

# Bedrock route prefixes that pick an API, not a different model.
_BEDROCK_ROUTES = ("converse/", "invoke/")


def normalize_provider(name: str | None) -> str | None:
    if not name:
        return None
    name = name.lower()
    if name.startswith("vertex_ai"):
        return "vertex_ai"
    return PROVIDER_ALIASES.get(name, name)


def split_model(key: str, provider: str) -> tuple[str, bool] | None:
    """Turn a LiteLLM model string into (model_id, had_geo_prefix).

    Returns None for Bedrock entries priced per AWS region, per commitment or
    per image size: they are prices of a model, not models.
    """
    rest = key.removeprefix(f"{provider}/")
    if not provider.startswith("bedrock"):
        return rest, False
    for route in _BEDROCK_ROUTES:
        rest = rest.removeprefix(route)
    if "/" in rest:
        return None
    head, sep, tail = rest.partition(".")
    if sep and tail and head in BEDROCK_GEO_PREFIXES:
        return tail, True
    return rest, False


def deployment_provider(litellm_params: dict) -> str | None:
    """The provider a deployment calls, as LiteLLM resolves it."""
    model = litellm_params.get("model") or ""
    explicit = litellm_params.get("custom_llm_provider")
    if explicit:
        return normalize_provider(explicit)
    if "/" in model:
        return normalize_provider(model.split("/", 1)[0])
    return None


def fetch_model_list(url: str) -> dict:
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
