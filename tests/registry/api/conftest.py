from datetime import UTC, date, datetime
from decimal import Decimal
from types import SimpleNamespace

import httpx
import pytest

from app.db.models import DBRegion
from app.registry.models import (
    DBRegistryAccessGroup,
    DBRegistryCredential,
    DBRegistryCredentialRegion,
    DBRegistryModel,
    DBRegistryModelPrice,
    DBRegistryModelRegion,
    DBRegistryModelRegionGroup,
    DBRegistryModelSupport,
    DBRegistryProvider,
    DBRegistryProxy,
)

US_URL = "http://litellm:4000"
EU_URL = "http://litellm-eu:4000"
RUN_FIELDS = {"id", "step", "started_at", "finished_at", "status", "stats", "error", "stale"}
MODEL_FIELDS = {
    "provider", "model_id", "mode", "max_input_tokens", "max_output_tokens",
    "input_cost_per_token", "output_cost_per_token", "prices", "eol_date",
    "override_eol", "blocked", "status", "family", "manufacturer", "source", "first_seen", "last_seen", "price_scopes",
    "proxies",
}
TODAY = date(2026, 1, 1)
CHECKED_AT = datetime(2026, 1, 2, 3, 4, 5, tzinfo=UTC)
# The problem texts of rule 4, the enable check.
UNKNOWN = "support is unknown: the support check has no result for this proxy"
UNSUPPORTED = "the proxy's LiteLLM version does not support the model"
UNAVAILABLE = "the proxy's cloud region does not offer the model"
PROVISIONED = "the proxy's cloud region offers the model only with provisioned throughput"
NEEDS_PROFILE = "the proxy's cloud region offers the model only through a geo profile; use the us. prefix"
BLOCKED = "the model is blocked"


@pytest.fixture
def models(registry_db):
    openai = DBRegistryProvider(name="openai")
    bedrock = DBRegistryProvider(name="bedrock")
    registry_db.add_all([openai, bedrock])
    registry_db.flush()
    common = {"source": "litellm", "first_seen": TODAY, "last_seen": TODAY}
    gpt = DBRegistryModel(
        provider_id=openai.id,
        model_id="gpt-4o",
        mode="chat",
        input_cost_per_token=Decimal("0.0000025"),
        output_cost_per_token=Decimal("0.00001"),
        prices={"input_cost_per_token": 2.5e-06},
        **common,
    )
    registry_db.add_all([
        gpt,
        DBRegistryModel(provider_id=openai.id, model_id="gpt-old", status="removed", **common),
        DBRegistryModel(
            provider_id=bedrock.id, model_id="anthropic.claude-x-v1:0",
            family="claudex", manufacturer="anthropic", **common,
        ),
    ])
    registry_db.flush()
    # The geo row goes in first, so the response order comes from the query.
    registry_db.add_all([
        DBRegistryModelPrice(
            model_id=gpt.id, scope_kind="geo", scope="eu", prices={"input_cost_per_token": 2.75e-06},
            source="litellm", last_seen=TODAY,
        ),
        DBRegistryModelPrice(
            model_id=gpt.id, scope_kind="base", scope="", prices={"input_cost_per_token": 2.5e-06},
            source="litellm", last_seen=TODAY,
        ),
    ])
    registry_db.commit()


class _FakeProxyClient:
    """Answers like a proxy from `live_proxies.by_url`; a URL that is not there is an empty proxy."""

    def __init__(self, proxies, url, key):
        self._proxies, self._url = proxies, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _answer(self):
        return self._proxies.by_url.get(self._url, {})

    def model_info(self):
        self._proxies.calls.append(self._url)
        answer = self._answer()
        if isinstance(answer, Exception):
            raise answer
        return answer.get("model_info", [])

    def alias_map(self):
        return self._answer().get("aliases", {})

    def credential_names(self):
        return set(self._answer().get("credentials", ()))


class _FakeProxyWriter:
    """Changes `live_proxies.by_url[url]` the way a proxy would, and puts each call in `live_proxies.writes`.

    `live_proxies.fail[(url, action)] = (status, body, saved)` makes a call fail; with `saved`, the change is
    made before the error, as LiteLLM can do.
    """

    def __init__(self, proxies, url, key):
        self._proxies, self._url = proxies, url

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        pass

    def _state(self):
        state = self._proxies.by_url.setdefault(self._url, {})
        for name, empty in (("model_info", list), ("aliases", dict), ("credentials", list)):
            state.setdefault(name, empty())
        return state

    def _call(self, action, change, *args):
        self._proxies.writes.append((self._url, action, *args))
        if hook := self._proxies.hooks.get(action):
            hook()
        failure = self._proxies.fail.get((self._url, action))
        if failure is None or failure[2]:
            change(self._state())
        if failure is not None:
            status, body, _ = failure
            request = httpx.Request("POST", f"{self._url}/{action}")
            response = httpx.Response(status, text=body, request=request)
            raise httpx.HTTPStatusError(body, request=request, response=response)
        return {}

    def create_model(self, payload):
        self._proxies.created += 1
        dep_id = f"new-{self._proxies.created}"

        def change(state):
            state["model_info"].append({
                "model_name": payload["model_name"],
                "litellm_params": dict(payload["litellm_params"]),
                "model_info": {**payload["model_info"], "id": dep_id, "db_model": True},
            })

        return self._call("create_model", change, payload)

    def update_model(self, dep_id, body):
        def change(state):
            for row in state["model_info"]:
                if row["model_info"].get("id") == dep_id:
                    if "model_name" in body:
                        row["model_name"] = body["model_name"]
                    row["litellm_params"] = {**row["litellm_params"], **body.get("litellm_params", {})}
                    row["model_info"] = {**row["model_info"], **body.get("model_info", {})}

        return self._call("update_model", change, dep_id, body)

    def delete_model(self, dep_id):
        def change(state):
            state["model_info"] = [row for row in state["model_info"] if row["model_info"].get("id") != dep_id]

        return self._call("delete_model", change, dep_id)

    def set_alias_map(self, full_map):
        def change(state):
            state["aliases"] = dict(full_map)

        return self._call("set_alias_map", change, full_map)

    def create_credential(self, name, values, info):
        def change(state):
            state["credentials"] = sorted({*state["credentials"], name})

        return self._call("create_credential", change, name, values, info)

    def update_credential(self, name, values, info):
        return self._call("update_credential", lambda state: None, name, values, info)

    def delete_credential(self, name):
        def change(state):
            state["credentials"] = [c for c in state["credentials"] if c != name]

        return self._call("delete_credential", change, name)


@pytest.fixture(autouse=True)
def live_proxies(monkeypatch):
    """No API test reaches the network: each read of a proxy goes to this fake and is put in `calls`, and each
    sync write goes to the fake writer and is put in `writes`."""
    proxies = SimpleNamespace(by_url={}, calls=[], writes=[], fail={}, hooks={}, created=0)
    monkeypatch.setattr(
        "app.registry.api.dry_run.ProxyClient", lambda url, key: _FakeProxyClient(proxies, url, key)
    )
    monkeypatch.setattr(
        "app.registry.api.sync.ProxyWriter", lambda url, key: _FakeProxyWriter(proxies, url, key)
    )
    return proxies


@pytest.fixture(autouse=True)
def managed_proxies(monkeypatch):
    """The proxy names that the registry sync may write to. Empty, so a write keeps its plain answer."""
    managed = set()
    for module in ("sync", "sync_runs", "routes"):
        monkeypatch.setattr(f"app.registry.api.{module}.registry_manages", lambda name: name in managed)
    return managed


def live_row(dep_id, name, model, credential=None, groups=(), db_model=True, **params):
    """One row of a proxy's `/model/info`. Extra keyword arguments go into `litellm_params`."""
    litellm_params = {"model": model, **params}
    if credential:
        litellm_params["litellm_credential_name"] = credential
    info = {"id": dep_id, "access_groups": list(groups), "db_model": db_model}
    return {"model_name": name, "litellm_params": litellm_params, "model_info": info}


def auth(token):
    return {"Authorization": f"Bearer {token}"}


def messages(response) -> list[str]:
    """The messages of a refusal, in order."""
    return [problem["message"] for problem in response.json()["problems"]]


def ok(response, status=200):
    assert response.status_code == status, response.text
    return response.json()


@pytest.fixture
def bare_region(registry_db):
    """A region with no registry_proxies row, so it is not a proxy of the registry."""
    region = DBRegion(name="local-ap1", litellm_api_url="http://ap", litellm_api_key="sk", is_active=True)
    registry_db.add(region)
    registry_db.commit()
    return region


def model(db, provider, model_id):
    return (
        db.query(DBRegistryModel)
        .join(DBRegistryProvider)
        .filter(DBRegistryProvider.name == provider, DBRegistryModel.model_id == model_id)
        .one()
    )


@pytest.fixture
def deployed(registry_db, models, proxy_region):
    """Two proxies with deployments and support rows; `gpt-old` has none."""
    eu = DBRegion(
        name="local-eu1", litellm_api_url=EU_URL, litellm_api_key="sk-test", is_active=True
    )
    registry_db.add(eu)
    registry_db.flush()
    gpt = model(registry_db, "openai", "gpt-4o")
    claude = model(registry_db, "bedrock", "anthropic.claude-x-v1:0")
    registry_db.add_all([
        DBRegistryProxy(region_id=proxy_region.id, cloud_regions={"bedrock": "us-east-1"}),
        DBRegistryProxy(region_id=eu.id),
        DBRegistryModelRegion(
            model_id=gpt.id, region_id=proxy_region.id, model_name="gpt-4o",
            litellm_model="openai/gpt-4o", litellm_deployment_id="us-gpt", enabled=True,
        ),
        DBRegistryModelRegion(
            model_id=gpt.id, region_id=eu.id, model_name="gpt-4o",
            litellm_model="openai/gpt-4o", litellm_deployment_id="eu-gpt", enabled=False,
        ),
        DBRegistryModelRegion(
            model_id=claude.id, region_id=proxy_region.id, model_name="claude-x",
            litellm_model="bedrock/us.anthropic.claude-x-v1:0", litellm_deployment_id="us-claude",
            enabled=True, litellm_params={"litellm_credential_name": "AWS Bedrock"},
        ),
        DBRegistryModelSupport(
            model_id=gpt.id, region_id=proxy_region.id, priced=True, supported=True,
            region_available=True, checked_at=CHECKED_AT,
        ),
        DBRegistryModelSupport(
            model_id=gpt.id, region_id=eu.id, priced=True, supported=False, checked_at=CHECKED_AT,
        ),
        DBRegistryModelSupport(
            model_id=claude.id, region_id=proxy_region.id, priced=True, supported=None, checked_at=CHECKED_AT,
        ),
    ])
    registry_db.commit()
    return {"us": proxy_region, "eu": eu}


def support_row(model_id=None, region_id=None, supported=True, region_available=True, priced=True):
    return DBRegistryModelSupport(
        model_id=model_id, region_id=region_id, priced=priced, supported=supported,
        region_available=region_available, checked_at=CHECKED_AT,
    )


def deployment_state(db):
    """Every deployment and group link, to show that a refused call or a dry run changed nothing."""
    db.expire_all()
    deployments = [
        (d.id, d.model_id, d.region_id, d.model_name, d.litellm_model, d.litellm_deployment_id, d.enabled,
         d.litellm_params, d.model_info, d.prices)
        for d in db.query(DBRegistryModelRegion).order_by(DBRegistryModelRegion.id)
    ]
    links = sorted((link.model_region_id, link.access_group_id) for link in db.query(DBRegistryModelRegionGroup))
    return deployments, links


def credential(db, name, provider=None, proxies=()):
    """A registry credential with no values, linked to the given regions."""
    row = DBRegistryCredential(name=name, provider=provider, credential_info={})
    db.add(row)
    db.flush()
    db.add_all(DBRegistryCredentialRegion(credential_id=row.id, region_id=region.id) for region in proxies)
    db.flush()
    return row


@pytest.fixture
def writable(registry_db, deployed):
    """The `deployed` proxies, imported, with the access groups alpha and beta and support rows for gpt-old."""
    old = model(registry_db, "openai", "gpt-old")
    registry_db.add_all([
        DBRegistryAccessGroup(slug="alpha", label="Alpha"),
        DBRegistryAccessGroup(slug="beta", label="Beta"),
        support_row(old.id, deployed["us"].id),
        support_row(old.id, deployed["eu"].id),
    ])
    for proxy in registry_db.query(DBRegistryProxy):
        proxy.imported_at = CHECKED_AT
    for name in ("c1", "c2", "AWS Bedrock"):
        credential(registry_db, name, proxies=deployed.values())
    registry_db.commit()
    return deployed
