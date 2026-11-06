"""Read-only access to LiteLLM: a proxy's admin API and the public model list."""

import re
from collections.abc import Set as AbstractSet
from urllib.parse import unquote_plus, urlsplit, urlunsplit

import httpx

from app.registry.config import BEDROCK_GEO_PREFIXES, HTTP_TIMEOUT_SECONDS, PROVIDER_ALIASES

# Bedrock route prefixes that pick an API, not a different model.
_BEDROCK_ROUTES = ("converse/", "invoke/")
_AWS_REGION_PATH = re.compile(r"^([a-z]{2}(?:-gov)?-[a-z]+-\d+)/([^/]+)$")
_MANTLE_HOST = re.compile(r"^bedrock-mantle\.([a-z]{2}(?:-gov)?-[a-z]+-\d+)\.api\.aws$")
# Azure data zones: `azure/eu/gpt-4o` is gpt-4o priced for the EU zone.
AZURE_DATA_ZONES = {"us", "eu", "global"}
# The key name parts LiteLLM masks on. The same rule here means the registry
# does not depend on that masking.
_SECRET_PARTS = {
    "password", "secret", "key", "token", "auth", "authorization", "cookie", "credential",
    "credentials", "access", "private", "certificate", "fingerprint", "tenancy", "apikey",
}
# Signed URLs and function keys carry their secret in these query names. In a
# config key, `code` is normal, so this set is for query names only.
_QUERY_SECRET_PARTS = {"sig", "signature", "code"}
_CAMEL_CASE_BOUNDARY = re.compile(r"([a-z0-9])([A-Z])")


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
    # A deployment can name the provider by an alias (`bedrock_converse/x`).
    head, sep, tail = key.partition("/")
    rest = tail if sep and normalize_provider(head) == provider else key
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


def is_env_ref(value) -> bool:
    """True for an `os.environ/NAME` value, which LiteLLM reads from the proxy's environment."""
    return isinstance(value, str) and value.startswith("os.environ/")


def _name_parts(name) -> set[str]:
    # Split `apiKey` like `api_key`, so a header name cannot hide a secret.
    return set(re.split(r"[_-]", _CAMEL_CASE_BOUNDARY.sub(r"\1_\2", str(name)).lower()))


def _is_secret_key(name) -> bool:
    parts = _name_parts(name)
    return bool(parts & _SECRET_PARTS) and "cost" not in parts


def _is_secret_query_name(name: str) -> bool:
    return _is_secret_key(name) or bool(_name_parts(name) & _QUERY_SECRET_PARTS)


def _without_url_secrets(value: str) -> str:
    """The URL without its user and password, secret query parameters and fragment."""
    try:
        url = urlsplit(value)
    except ValueError:
        return value
    if not (url.scheme and url.netloc):
        return value
    # Split by hand so the parameters that stay keep their exact encoding.
    query = "&".join(
        part for part in url.query.split("&") if not _is_secret_query_name(unquote_plus(part.partition("=")[0]))
    )
    # A fragment never reaches the server, so it holds no config, but it can hold a token.
    return urlunsplit(url._replace(netloc=url.netloc.rpartition("@")[2], query=query, fragment=""))


def without_secrets(value):
    """A copy of `value` with every secret value removed.

    A secret-like key stays only with an `os.environ/` reference. A credential
    name is not a secret.
    """
    if isinstance(value, list):
        return [without_secrets(item) for item in value]
    if isinstance(value, str):
        return _without_url_secrets(value)
    if not isinstance(value, dict):
        return value
    clean = {}
    for key, item in value.items():
        if key == "litellm_credential_name" and isinstance(item, str):
            clean[key] = item
        elif _is_secret_key(key):
            if is_env_ref(item):
                clean[key] = item
        else:
            clean[key] = without_secrets(item)
    return clean


def mantle_region(api_base) -> str | None:
    """The AWS region of a Bedrock Mantle `api_base` URL, or None for any other value."""
    if not isinstance(api_base, str):
        return None
    try:
        host = urlsplit(api_base).hostname
    except ValueError:
        return None
    match = _MANTLE_HOST.match(host or "")
    return match[1] if match else None


def registry_provider_and_model(litellm_params: dict, model_info: dict | None = None) -> tuple[str | None, str]:
    """(registry provider, model string to split) for a deployment.

    The registry provider is the upstream service that answers the call. On the
    OpenAI-compatible route to Bedrock Mantle, LiteLLM says `openai` instead.
    With no explicit provider and no prefix, use the provider that LiteLLM
    found for the model in `/model/info`.
    """
    model = litellm_params.get("model") or ""
    explicit = litellm_params.get("custom_llm_provider")
    if explicit:
        provider = normalize_provider(explicit)
    elif "/" in model:
        provider = normalize_provider(model.split("/", 1)[0])
    else:
        provider = normalize_provider((model_info or {}).get("litellm_provider"))
    # Azure's model string names our deployment; base_model names the model.
    source_model = (model_info or {}).get("base_model") or model
    if provider == "openai" and mantle_region(litellm_params.get("api_base")) is not None:
        return "bedrock_mantle", source_model.removeprefix("openai/")
    return provider, source_model


def without_alias_copies(deployments: list[dict], aliases: AbstractSet[str]) -> tuple[list[dict], list[str]]:
    """The deployments with no alias copies, and the names of the copies.

    `/model/info` lists each alias again, with the id of its target.
    """
    real_ids = {
        (dep.get("model_info") or {}).get("id") for dep in deployments if dep.get("model_name") not in aliases
    } - {None}
    kept, copies, kept_ids = [], [], set()
    for dep in deployments:
        dep_id = (dep.get("model_info") or {}).get("id")
        if dep.get("model_name") in aliases and (dep_id in real_ids or dep_id in kept_ids):
            # Skip only a row that repeats a deployment listed under a real name or kept before it.
            copies.append(dep.get("model_name"))
            continue
        kept.append(dep)
        if dep_id is not None:
            kept_ids.add(dep_id)
    return kept, copies


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

    def alias_map(self) -> dict:
        """The raw `model_group_alias` map, with no fallback on error.

        A value is a target name, or `{"model": <target>, "hidden": true}`.
        """
        fields = self._get("/router/settings").get("fields") or []
        value = next((f.get("field_value") for f in fields if f.get("field_name") == "model_group_alias"), None)
        return value if isinstance(value, dict) else {}

    def version(self) -> str | None:
        try:
            return self._get("/openapi.json").get("info", {}).get("version")
        except httpx.HTTPError:
            return None

    def credential_names(self) -> set[str]:
        """The credential names, with no fallback on error: a planner must know when it could not read them."""
        credentials = self._get("/credentials").get("credentials") or []
        return {cred["credential_name"] for cred in credentials if cred.get("credential_name")}

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
