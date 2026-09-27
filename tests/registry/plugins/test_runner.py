from datetime import date
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.registry.discovery import apply_model_list
from app.registry.models import (
    DBRegistryCloudAvailability,
    DBRegistryModel,
    DBRegistryModelLifecycle,
    DBRegistryModelPrice,
    DBRegistryProvider,
    DBRegistryRun,
)
from app.registry.plugins.runner import apply_plugin, run_plugins, validate
from app.registry.prices import apply_prices

TODAY = date(2026, 9, 27)


def _out(models, source="test_src"):
    return {"schema": 1, "source": source, "models": models}


def test_validate_normalizes_scopes_dates_and_regions():
    records = validate(
        _out([
            {"provider": "bedrock", "model_id": "m", "max_input_tokens": 100,
             "prices": {"base": {"input_cost_per_token": 1e-06}, "geo:us": {"input_cost_per_token": 1.1e-06},
                        "region:us-east-1": {"input_cost_per_token": 1.2e-06}},
             "lifecycle": {"status": "LEGACY", "eol_date": "2027-01-08"},
             "regions": [{"cloud_region": "us-east-1", "call_types": ["ON_DEMAND"]}]},
            {"provider": "bedrock", "model_id": "m", "prices": {}},
        ]),
        "test_src",
    )
    assert len(records) == 1
    rec = records[0]
    assert set(rec["prices"]) == {("base", ""), ("geo", "us"), ("cloud_region", "us-east-1")}
    assert rec["lifecycle"]["eol_date"] == date(2027, 1, 8) and rec["lifecycle"]["legacy_at"] is None
    assert rec["regions"] == {"us-east-1": ["ON_DEMAND"]}


@pytest.mark.parametrize(
    "output",
    [
        {"schema": 2, "source": "test_src", "models": []},
        {"schema": 1, "source": "other", "models": []},
        _out("not a list"),
        _out([{"model_id": "m"}]),
        _out([{"provider": "p", "model_id": "m", "prices": {"base": {"input_cost_per_token": -1}}}]),
        _out([{"provider": "p", "model_id": "m", "prices": {"base": {"input_cost_per_token": True}}}]),
        _out([{"provider": "p", "model_id": "m", "prices": {"base": {"speed": 1}}}]),
        _out([{"provider": "p", "model_id": "m", "prices": {"zone:eu": {"input_cost_per_token": 1}}}]),
        _out([{"provider": "p", "model_id": "m", "max_input_tokens": -5}]),
        _out([{"provider": "p", "model_id": "m", "lifecycle": {"eol_date": "soon"}}]),
    ],
)
def test_validate_rejects_bad_output(output):
    with pytest.raises(ValueError):
        validate(output, "test_src")


def _model(db, provider_name, model_id, mode=None, prices=None, source="litellm"):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, mode=mode, supports=[], status="active",
        source=source, prices=prices or {}, first_seen=TODAY, last_seen=TODAY,
    )
    db.add(row)
    db.flush()
    return row


def _price(db, model, source, value, kind="base", scope=""):
    db.add(DBRegistryModelPrice(model_id=model.id, scope_kind=kind, scope=scope,
                                prices={"input_cost_per_token": value}, source=source, last_seen=TODAY))
    db.flush()


def test_override_beats_litellm_and_litellm_leaves_it_alone(registry_db):
    model = _model(registry_db, "deepinfra", "m", prices={"input_cost_per_token": 1e-06})
    _price(registry_db, model, "litellm", 1e-06)
    registry_db.commit()

    records = validate(_out([{"provider": "deepinfra", "model_id": "m", "prices": {"base": {"input_cost_per_token": 2e-06}}}]), "test_src")
    stats = apply_plugin(registry_db, "test_src", "override", records, TODAY)
    registry_db.commit()
    assert stats["priced"] == 1
    row = registry_db.get(DBRegistryModelPrice, (model.id, "base", ""))
    assert row.source == "test_src" and row.prices == {"input_cost_per_token": 2e-06}
    assert model.prices == {"input_cost_per_token": 2e-06}

    with (
        patch("app.registry.prices.override_sources", return_value={"test_src"}),
        patch("app.registry.discovery.override_sources", return_value={"test_src"}),
    ):
        listed = {("deepinfra", "m"): {"prices": {"input_cost_per_token": 1e-06}, "input_cost_per_token": 1e-06, "mode": "chat"}}
        apply_model_list(registry_db, listed, date(2026, 9, 28))
        apply_prices(registry_db, {("deepinfra", "m")}, {("deepinfra", "m"): {("base", ""): {"input_cost_per_token": 1e-06}}}, date(2026, 9, 28))
        registry_db.commit()
    row = registry_db.get(DBRegistryModelPrice, (model.id, "base", ""))
    assert row.source == "test_src" and row.prices == {"input_cost_per_token": 2e-06}
    # The headline price stays the plugin's; other list fields still update.
    assert model.prices == {"input_cost_per_token": 2e-06} and model.mode == "chat"


def test_fill_only_prices_unpriced_models(registry_db):
    priced = _model(registry_db, "deepinfra", "priced")
    _price(registry_db, priced, "litellm", 1e-06)
    free = _model(registry_db, "deepinfra", "free", source="proxy")
    registry_db.commit()
    records = validate(_out([
        {"provider": "deepinfra", "model_id": "priced", "prices": {"base": {"input_cost_per_token": 5e-06}}},
        {"provider": "deepinfra", "model_id": "free", "prices": {"base": {"input_cost_per_token": 5e-06}}},
        {"provider": "deepinfra", "model_id": "unknown", "prices": {"base": {"input_cost_per_token": 5e-06}}},
    ]), "test_src")

    stats = apply_plugin(registry_db, "test_src", "fill", records, TODAY)
    registry_db.commit()

    assert stats["matched"] == 2 and stats["unmatched"] == 1 and stats["priced"] == 1
    assert registry_db.get(DBRegistryModelPrice, (priced.id, "base", "")).source == "litellm"
    assert registry_db.get(DBRegistryModelPrice, (free.id, "base", "")).source == "test_src"


def test_specs_lifecycle_and_regions(registry_db):
    model = _model(registry_db, "bedrock", "m", mode="chat")
    registry_db.commit()

    def run(regions):
        records = validate(_out([{"provider": "bedrock", "model_id": "m", "mode": "embedding", "max_input_tokens": 100,
                                  "lifecycle": {"status": "ACTIVE", "launched_at": "2026-01-01"},
                                  "regions": regions}]), "test_src")
        apply_plugin(registry_db, "test_src", "fill", records, TODAY)
        registry_db.commit()

    run([{"cloud_region": "us-east-1"}, {"cloud_region": "eu-west-1"}])
    assert model.mode == "chat" and model.max_input_tokens == 100  # fills gaps only
    life = registry_db.get(DBRegistryModelLifecycle, ("bedrock", "m", "test_src"))
    assert life.status == "ACTIVE" and life.launched_at == date(2026, 1, 1)

    run([{"cloud_region": "us-east-1"}])
    regions = {r.cloud_region for r in registry_db.query(DBRegistryCloudAvailability).filter_by(source="test_src")}
    assert regions == {"us-east-1"}


def test_guard_refuses_a_source_that_dropped_most_models(registry_db):
    for i in range(4):
        _price(registry_db, _model(registry_db, "deepinfra", f"m{i}"), "test_src", 1e-06)
    registry_db.commit()
    records = validate(_out([{"provider": "deepinfra", "model_id": "m0"}]), "test_src")

    with pytest.raises(RuntimeError, match="refusing"):
        apply_plugin(registry_db, "test_src", "override", records, TODAY)


def test_run_plugins_isolates_failures(registry_db):
    _model(registry_db, "deepinfra", "m")
    registry_db.commit()
    good = SimpleNamespace(PRICE_ROLE="fill", parse=lambda: _out(
        [{"provider": "deepinfra", "model_id": "m", "prices": {"base": {"input_cost_per_token": 1e-06}}}], "good"))
    bad = SimpleNamespace(PRICE_ROLE="fill", parse=lambda: {"schema": 1, "source": "bad", "models": "x"})

    with patch("app.registry.plugins.runner.load_plugins", return_value={"bad": bad, "broken": ValueError("SOURCE is missing"), "good": good}):
        with pytest.raises(RuntimeError, match="bad, broken"):
            run_plugins(registry_db, TODAY)

    runs = {r.step: r.status for r in registry_db.query(DBRegistryRun)}
    assert runs == {"plugin:bad": "failed", "plugin:broken": "failed", "plugin:good": "ok"}
    assert registry_db.query(DBRegistryModelPrice).filter_by(source="good").count() == 1


def test_list_does_not_blank_a_spec_a_plugin_filled(registry_db):
    model = _model(registry_db, "deepinfra", "m")
    registry_db.commit()
    records = validate(_out([{"provider": "deepinfra", "model_id": "m", "mode": "chat", "max_input_tokens": 100}]), "test_src")
    apply_plugin(registry_db, "test_src", "fill", records, TODAY)
    registry_db.commit()

    stats = apply_model_list(registry_db, {("deepinfra", "m"): {"mode": None, "max_input_tokens": None}}, date(2026, 9, 28))

    assert stats["updated"] == 0
    assert model.mode == "chat" and model.max_input_tokens == 100


def test_lifecycle_and_regions_are_kept_for_unknown_models(registry_db):
    records = validate(_out([{"provider": "bedrock", "model_id": "not-in-registry",
                              "lifecycle": {"status": "ACTIVE"}, "regions": [{"cloud_region": "us-east-1"}]}]), "test_src")

    stats = apply_plugin(registry_db, "test_src", "fill", records, TODAY)
    registry_db.commit()

    assert stats["unmatched"] == 1
    assert registry_db.get(DBRegistryModelLifecycle, ("bedrock", "not-in-registry", "test_src")) is not None
    assert registry_db.query(DBRegistryCloudAvailability).filter_by(model_id="not-in-registry").count() == 1


def test_a_dropped_model_loses_its_regions_but_keeps_its_lifecycle(registry_db):
    def run(model_ids, day):
        records = validate(_out([{"provider": "bedrock", "model_id": m, "lifecycle": {"status": "ACTIVE"},
                                  "regions": [{"cloud_region": "us-east-1"}]} for m in model_ids]), "test_src")
        apply_plugin(registry_db, "test_src", "fill", records, day)
        registry_db.commit()

    run(["a", "b"], TODAY)
    run(["a"], date(2026, 9, 28))

    assert {r.model_id for r in registry_db.query(DBRegistryCloudAvailability)} == {"a"}
    assert {r.model_id for r in registry_db.query(DBRegistryModelLifecycle)} == {"a", "b"}


def test_guard_counts_only_the_last_run(registry_db):
    def run(model_ids, day):
        records = validate(_out([{"provider": "bedrock", "model_id": m, "lifecycle": {"status": "ACTIVE"}}
                                 for m in model_ids]), "test_src")
        apply_plugin(registry_db, "test_src", "fill", records, day)
        registry_db.commit()

    # The list changes by half each day. Counting all six models ever seen,
    # day 3 would keep 2 of 6 and be refused; against day 2 it keeps 2 of 4.
    run(["x1", "x2", "x3", "x4"], date(2026, 9, 1))
    run(["x3", "x4", "y1", "y2"], date(2026, 9, 2))
    run(["y1", "y2", "y3", "y4"], date(2026, 9, 3))

    with pytest.raises(RuntimeError, match="refusing"):
        run(["y1"], date(2026, 9, 4))
