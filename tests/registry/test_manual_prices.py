from datetime import date
from decimal import Decimal

from app.registry.discovery import apply_model_list, entry_fields
from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider
from app.registry.plugins.runner import apply_plugin, validate
from app.registry.prices import apply_prices, price_fields, set_manual_prices

TODAY = date(2026, 10, 1)
MANUAL = {"input_cost_per_token": 1e-06, "output_cost_per_token": 2e-06}


def _model(db, provider_name="bedrock", model_id="anthropic.claude-x-v1:0"):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, supports=[], status="active", source="litellm",
        first_seen=date(2026, 9, 1), last_seen=date(2026, 9, 1),
    )
    db.add(row)
    db.flush()
    return row


def _price(db, model, kind, scope, prices, source):
    db.add(
        DBRegistryModelPrice(
            model_id=model.id, scope_kind=kind, scope=scope, prices=prices, source=source, last_seen=TODAY,
        )
    )
    db.flush()


def _rows(db, model):
    return {
        (p.scope_kind, p.scope): (p.source, p.prices)
        for p in db.query(DBRegistryModelPrice).filter_by(model_id=model.id)
    }


def test_a_price_object_replaces_the_whole_row_of_any_source(registry_db):
    model = _model(registry_db)
    _price(registry_db, model, "geo", "us", {"input_cost_per_token": 3e-06, "cache_read_cost": 1e-07}, "litellm")

    set_manual_prices(registry_db, model, {"geo:us": {"output_cost_per_token": 4e-06}}, TODAY)
    registry_db.commit()

    assert _rows(registry_db, model) == {("geo", "us"): ("manual", {"output_cost_per_token": 4e-06})}


def test_none_removes_only_a_manual_row(registry_db):
    model = _model(registry_db)
    _price(registry_db, model, "geo", "us", {"input_cost_per_token": 3e-06}, "litellm")
    _price(registry_db, model, "geo", "eu", {"input_cost_per_token": 5e-06}, "manual")

    set_manual_prices(registry_db, model, {"geo:us": None, "geo:eu": None}, TODAY)
    registry_db.commit()

    assert _rows(registry_db, model) == {("geo", "us"): ("litellm", {"input_cost_per_token": 3e-06})}


def test_updated_at_moves_only_when_a_price_row_changes(registry_db):
    model = _model(registry_db)
    _price(registry_db, model, "geo", "us", MANUAL, "manual")
    registry_db.commit()
    before = model.updated_at

    set_manual_prices(registry_db, model, {"geo:us": MANUAL, "geo:eu": None}, TODAY)
    assert model.updated_at == before

    set_manual_prices(registry_db, model, {"geo:us": None}, TODAY)
    deleted = model.updated_at
    assert deleted > before

    set_manual_prices(registry_db, model, {"geo:eu": MANUAL}, TODAY)
    assert model.updated_at > deleted


def test_a_base_price_is_the_headline_until_it_is_removed(registry_db):
    model = _model(registry_db)

    set_manual_prices(registry_db, model, {"base": MANUAL}, TODAY)
    registry_db.commit()
    registry_db.refresh(model)
    assert model.prices == MANUAL
    assert model.input_cost_per_token == Decimal("1e-06")
    assert model.output_cost_per_token == Decimal("2e-06")

    set_manual_prices(registry_db, model, {"base": None}, TODAY)
    registry_db.commit()
    registry_db.refresh(model)
    assert model.prices == {}
    assert model.input_cost_per_token is None and model.output_cost_per_token is None
    assert _rows(registry_db, model) == {}


LIST_ENTRY = {"input_cost_per_token": 3e-06, "output_cost_per_token": 6e-06, "mode": "chat"}
LIST_GEO = {"input_cost_per_token": 3.3e-06}
MANUAL_GEO = {"input_cost_per_token": 1.5e-06}
IDENT = ("deepinfra", "m")


def _list_run(db, today):
    """The model list step as the daily job runs it, with the real kept sources."""
    listed = apply_model_list(db, {IDENT: entry_fields(LIST_ENTRY)}, today)
    db.commit()
    prices = apply_prices(db, {IDENT}, {IDENT: {("base", ""): price_fields(LIST_ENTRY), ("geo", "us"): LIST_GEO}}, today)
    db.commit()
    return listed, prices


def _plugin_run(db, role, today):
    records = validate(
        {"schema": 1, "source": "test_src", "models": [{
            "provider": "deepinfra", "model_id": "m",
            "prices": {"base": {"input_cost_per_token": 2e-06}, "geo:us": {"input_cost_per_token": 2.2e-06},
                       "region:us-east-1": {"input_cost_per_token": 2.4e-06}},
        }]},
        "test_src",
    )
    stats = apply_plugin(db, "test_src", role, records, today)
    db.commit()
    return stats


def _listed_model(db):
    db.add(DBRegistryProvider(name="deepinfra"))
    db.commit()
    _list_run(db, TODAY)
    return db.query(DBRegistryModel).filter_by(model_id="m").one()


def test_daily_steps_keep_manual_prices_and_the_headline(registry_db):
    model = _listed_model(registry_db)
    assert _rows(registry_db, model) == {
        ("base", ""): ("litellm", price_fields(LIST_ENTRY)), ("geo", "us"): ("litellm", LIST_GEO),
    }

    set_manual_prices(registry_db, model, {"base": MANUAL, "geo:us": MANUAL_GEO}, TODAY)
    registry_db.commit()

    for today in (date(2026, 10, 2), date(2026, 10, 3)):
        listed, prices = _list_run(registry_db, today)
        plugin = _plugin_run(registry_db, "override", today)

    assert _rows(registry_db, model) == {
        ("base", ""): ("manual", MANUAL),
        ("geo", "us"): ("manual", MANUAL_GEO),
        ("cloud_region", "us-east-1"): ("test_src", {"input_cost_per_token": 2.4e-06}),
    }
    registry_db.refresh(model)
    assert model.prices == MANUAL
    assert model.input_cost_per_token == Decimal("1e-06")
    assert listed["updated"] == 0
    assert prices["price_changes"] == 0 and prices["price_scopes_dropped"] == 0
    assert plugin["price_changes"] == 0


def test_a_fill_plugin_steps_aside_for_a_manual_price(registry_db):
    model = _model(registry_db, "deepinfra", "m")
    set_manual_prices(registry_db, model, {"base": MANUAL}, TODAY)
    registry_db.commit()

    _plugin_run(registry_db, "fill", TODAY)

    assert _rows(registry_db, model) == {("base", ""): ("manual", MANUAL)}
    registry_db.refresh(model)
    assert model.prices == MANUAL


def test_the_list_prices_the_model_again_after_its_manual_base_is_removed(registry_db):
    model = _listed_model(registry_db)
    set_manual_prices(registry_db, model, {"base": MANUAL}, TODAY)
    registry_db.commit()

    set_manual_prices(registry_db, model, {"base": None}, TODAY)
    registry_db.commit()
    registry_db.refresh(model)
    assert model.prices == {}

    _list_run(registry_db, date(2026, 10, 2))
    registry_db.refresh(model)
    assert _rows(registry_db, model)[("base", "")] == ("litellm", price_fields(LIST_ENTRY))
    assert model.prices == price_fields(LIST_ENTRY)
