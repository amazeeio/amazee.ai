import json
from datetime import UTC, date, datetime

import httpx
import pytest

from app.registry.eol import apply_eol
from app.registry.models import (
    DBRegistryModel,
    DBRegistryModelLifecycle,
    DBRegistryModelPrice,
    DBRegistryProvider,
)
from tests.registry.api.conftest import TODAY, US_URL, auth, live_row, model, ok

GPT = "/registry/models/openai/gpt-4o"
CLAUDE = "/registry/models/bedrock/anthropic.claude-x-v1:0"
PRICE = {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}
X = date(2026, 12, 1)
Y = date(2027, 6, 1)


def _patch(client, token, path, body):
    return client.patch(path, json=body, headers=auth(token))


def _state(db):
    """Every stored field the PATCH can change, to check that nothing was saved."""
    db.expire_all()
    models = [
        (m.id, m.family, m.override_eol, m.blocked, m.eol_date, m.prices,
         m.input_cost_per_token, m.output_cost_per_token, m.updated_at)
        for m in db.query(DBRegistryModel).order_by(DBRegistryModel.id)
    ]
    prices = [
        (p.model_id, p.scope_kind, p.scope, p.source, p.prices, p.last_seen)
        for p in db.query(DBRegistryModelPrice).order_by(
            DBRegistryModelPrice.model_id, DBRegistryModelPrice.scope_kind, DBRegistryModelPrice.scope
        )
    ]
    return models, prices


def test_patch_needs_a_system_admin(client, test_token, models):
    assert client.patch(GPT, json={"blocked": True}).status_code == 401
    assert client.patch(GPT, json={"blocked": True}, headers=auth(test_token)).status_code == 403


def test_patch_changes_only_the_fields_in_the_body(client, admin_token, models):
    body = ok(_patch(client, admin_token, GPT, {"family": "gpt4", "blocked": True}))
    assert body["family"] == "gpt4" and body["blocked"] is True
    assert body["override_eol"] is None and body["mode"] == "chat"

    body = ok(_patch(client, admin_token, GPT, {"override_eol": "2027-01-01"}))
    assert body["override_eol"] == body["eol_date"] == "2027-01-01"
    assert body["family"] == "gpt4" and body["blocked"] is True

    body = ok(_patch(client, admin_token, CLAUDE, {"family": None}))
    assert body["family"] is None and body["manufacturer"] == "anthropic"


def test_patch_moves_updated_at_only_on_a_real_change(client, admin_token, registry_db, models):
    # A fixed old time, so the test never compares the database clock with the Python clock.
    old = datetime(2020, 1, 1, tzinfo=UTC)

    def moved(change):
        model(registry_db, "openai", "gpt-4o").updated_at = old
        registry_db.commit()
        ok(_patch(client, admin_token, GPT, change))
        registry_db.expire_all()
        return model(registry_db, "openai", "gpt-4o").updated_at != old

    assert not moved({"family": None, "blocked": False, "override_eol": None})
    for change in ({"family": "gpt4"}, {"blocked": True}, {"override_eol": X.isoformat()}):
        assert moved(change), change


def test_patch_writes_manual_prices(client, admin_token, registry_db, models):
    # A null removes only a manual price, so the list's geo price stays.
    body = ok(_patch(client, admin_token, GPT, {"prices": {"geo:eu": None}}))
    assert {(s["scope_kind"], s["scope"], s["source"]) for s in body["price_scopes"]} == {
        ("base", "", "litellm"), ("geo", "eu", "litellm"),
    }

    body = ok(_patch(client, admin_token, GPT, {"prices": {"base": PRICE, "geo:eu": PRICE, "region:us-east-1": PRICE}}))

    assert {(s["scope_kind"], s["scope"], s["source"]) for s in body["price_scopes"]} == {
        ("base", "", "manual"), ("geo", "eu", "manual"), ("cloud_region", "us-east-1", "manual"),
    }
    assert all(s["prices"] == PRICE for s in body["price_scopes"])
    assert body["prices"] == PRICE
    assert body["input_cost_per_token"] == 1e-06 and body["output_cost_per_token"] == 2e-06
    registry_db.expire_all()
    assert model(registry_db, "openai", "gpt-4o").prices == PRICE


def test_patch_takes_a_whole_number_as_a_price(client, admin_token, models):
    body = ok(_patch(client, admin_token, GPT, {"prices": {"base": {"input_cost_per_token": 1}}}))

    assert body["prices"] == {"input_cost_per_token": 1}


def test_override_eol_beats_the_sources_and_the_daily_step_agrees(client, admin_token, registry_db, models):
    registry_db.add(DBRegistryModelLifecycle(
        provider="openai", model_id="gpt-4o", source="litellm", eol_date=Y, last_seen=TODAY,
    ))
    registry_db.commit()

    assert ok(_patch(client, admin_token, GPT, {"override_eol": X.isoformat()}))["eol_date"] == X.isoformat()
    assert apply_eol(registry_db)["changed"] == 0

    assert ok(_patch(client, admin_token, GPT, {"override_eol": None}))["eol_date"] == Y.isoformat()
    assert apply_eol(registry_db)["changed"] == 0


def test_dry_run_returns_the_change_and_saves_nothing(client, admin_token, registry_db, models):
    before = _state(registry_db)

    body = ok(client.patch(
        f"{GPT}?dry_run=true",
        json={"family": "gpt4", "override_eol": X.isoformat(), "blocked": True, "prices": {"base": PRICE}},
        headers=auth(admin_token),
    ))

    result = body["result"]
    assert result["family"] == "gpt4" and result["blocked"] is True
    assert result["override_eol"] == result["eol_date"] == X.isoformat()
    assert result["prices"] == PRICE
    assert _state(registry_db) == before


def test_price_dry_run_plans_the_price_of_each_live_deployment(client, admin_token, deployed, live_proxies):
    row = live_row("us-gpt", "gpt-4o", "openai/gpt-4o", input_cost_per_token=5e-06)
    live_proxies.by_url[US_URL] = {"model_info": [row]}

    body = ok(_patch(client, admin_token, f"{GPT}?dry_run=true", {"prices": {"base": PRICE}}))

    assert body["result"]["prices"] == PRICE
    assert body["steps"] == [{
        "proxy": "local-us1", "action": "update_model", "model": "openai/gpt-4o", "model_name": "gpt-4o",
        "deployment_id": "us-gpt", "payload": None,
        "changes": {"prices": {"from": {"input_cost_per_token": 5e-06, "output_cost_per_token": None}, "to": PRICE}},
        "problems": [],
    }]
    # The EU deployment is turned off, so its proxy is not read.
    assert live_proxies.calls == [US_URL]


def test_family_dry_run_plans_nothing(client, admin_token, deployed, live_proxies):
    body = ok(_patch(client, admin_token, f"{GPT}?dry_run=true", {"family": "gpt4", "blocked": True}))

    assert body["steps"] == []
    assert live_proxies.calls == []


def test_price_write_reads_no_proxy(client, admin_token, deployed, live_proxies):
    body = ok(_patch(client, admin_token, GPT, {"prices": {"base": PRICE}}))

    assert body["prices"] == PRICE
    assert live_proxies.calls == []


def test_price_dry_run_with_a_proxy_that_cannot_be_read(client, admin_token, deployed, live_proxies):
    live_proxies.by_url[US_URL] = httpx.ConnectError("down")

    body = ok(_patch(client, admin_token, f"{GPT}?dry_run=true", {"prices": {"base": PRICE}}))

    assert body["steps"] == [{
        "proxy": "local-us1", "action": "read_proxy", "model": None, "model_name": None, "deployment_id": None,
        "payload": None, "changes": None, "problems": ["the proxy could not be read: ConnectError"],
    }]


def test_patch_reaches_a_model_id_with_a_slash(client, admin_token, registry_db, models):
    deepinfra = DBRegistryProvider(name="deepinfra")
    registry_db.add(deepinfra)
    registry_db.flush()
    registry_db.add(DBRegistryModel(
        provider_id=deepinfra.id, model_id="BAAI/bge-m3", source="litellm", first_seen=TODAY, last_seen=TODAY,
    ))
    registry_db.commit()

    body = ok(_patch(client, admin_token, "/registry/models/deepinfra/BAAI/bge-m3", {"family": "bge"}))

    assert (body["provider"], body["model_id"], body["family"]) == ("deepinfra", "BAAI/bge-m3", "bge")


def test_new_and_eol_keep_their_get_handlers(client, admin_token, models):
    for path in ("/registry/models/new", "/registry/models/eol"):
        response = client.get(path, headers=auth(admin_token))
        assert response.status_code == 200 and isinstance(response.json(), list)


@pytest.mark.parametrize("key", ["openai/nope", "gpt-4o", "nope/gpt-4o"])
def test_patch_unknown_model_is_404(client, admin_token, models, key):
    assert _patch(client, admin_token, f"/registry/models/{key}", {"blocked": True}).status_code == 404


def _price(scope="base", **fields):
    return {"prices": {scope: fields or PRICE}}


@pytest.mark.parametrize(
    "body",
    [
        [],
        "x",
        {},
        {"unknown": 1},
        {"family": ""},
        {"family": 5},
        {"blocked": None},
        {"blocked": "yes"},
        {"override_eol": "soon"},
        {"override_eol": 0},
        {"override_eol": 10**12},
        {"override_eol": "0"},
        {"override_eol": "1798761600"},
        {"override_eol": "2027-01-01T00:00:00"},
        {"prices": None},
        {"prices": {}},
        _price("zone:eu"),
        _price("geo:"),
        _price("geo:US"),
        _price("region:us_east_1"),
        {"prices": {"base": {}}},
        _price(speed=1),
        _price(Input_cost_per_token=1e-06),
        _price(input_cost_per_token=-1),
        _price(input_cost_per_token=True),
        _price(input_cost_per_token="1e-06"),
        _price(input_cost_per_token={"low": 1e-06}),
        # json.dumps writes the JSON literal Infinity, which Python's parser reads back.
        _price(input_cost_per_token=float("inf")),
        {"family": float("nan")},
    ],
)
def test_patch_rejects_a_bad_body(client, admin_token, registry_db, models, body):
    before = _state(registry_db)

    response = client.patch(
        GPT, content=json.dumps(body), headers={**auth(admin_token), "Content-Type": "application/json"}
    )

    assert response.status_code == 422, response.text
    assert _state(registry_db) == before
