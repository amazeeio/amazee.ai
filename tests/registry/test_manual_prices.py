from datetime import date
from decimal import Decimal

from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider
from app.registry.prices import set_manual_prices

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


def test_scope_keys_give_their_scope_kinds(registry_db):
    model = _model(registry_db)

    set_manual_prices(registry_db, model, {"base": MANUAL, "geo:eu": MANUAL, "region:us-east-1": MANUAL}, TODAY)
    registry_db.commit()

    assert set(_rows(registry_db, model)) == {("base", ""), ("geo", "eu"), ("cloud_region", "us-east-1")}
