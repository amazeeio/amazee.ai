from datetime import date

from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider
from app.registry.prices import apply_prices, parse_prices, pick_price, price_fields

LIST = {
    "anthropic.claude-x-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 3e-06},
    "us.anthropic.claude-x-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 3.3e-06},
    "bedrock/converse/us.anthropic.claude-x-v1:0": {"litellm_provider": "bedrock_converse", "input_cost_per_token": 9e-06},
    "bedrock/us-gov-west-1/anthropic.claude-x-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 3.6e-06},
    "bedrock/us-east-1/1-month-commitment/anthropic.claude-x-v1:0": {"litellm_provider": "bedrock", "input_cost_per_second": 1},
    "azure/eu/gpt-4o": {"litellm_provider": "azure", "input_cost_per_token": 2.75e-06},
    "azure/gpt-4o": {"litellm_provider": "azure", "input_cost_per_token": 2.5e-06},
    "vertex_ai/veo-3": {"litellm_provider": "vertex_ai-video-models", "output_cost_per_second": 0.4},
    "no-price": {"litellm_provider": "bedrock", "mode": "chat"},
}


def test_parse_prices_keeps_every_scope():
    prices = parse_prices(LIST, {"bedrock", "azure", "vertex_ai"})

    assert prices[("bedrock", "anthropic.claude-x-v1:0")] == {
        ("base", ""): {"input_cost_per_token": 3e-06},
        # The first key wins when a route prefix repeats a scope.
        ("geo", "us"): {"input_cost_per_token": 3.3e-06},
        ("cloud_region", "us-gov-west-1"): {"input_cost_per_token": 3.6e-06},
    }
    assert prices[("azure", "gpt-4o")] == {
        ("geo", "eu"): {"input_cost_per_token": 2.75e-06},
        ("base", ""): {"input_cost_per_token": 2.5e-06},
    }
    assert prices[("vertex_ai", "veo-3")] == {("base", ""): {"output_cost_per_second": 0.4}}
    assert ("bedrock", "no-price") not in prices


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


def test_apply_prices_syncs_scopes(registry_db):
    model = _model(registry_db)
    ident = ("bedrock", "anthropic.claude-x-v1:0")
    prices = parse_prices(LIST, {"bedrock"})

    first = apply_prices(registry_db, {ident}, prices, date(2026, 9, 27))
    registry_db.commit()
    assert first == {"price_scopes": 3, "price_changes": 3, "price_scopes_dropped": 0}

    again = apply_prices(registry_db, {ident}, prices, date(2026, 9, 28))
    assert again == {"price_scopes": 3, "price_changes": 0, "price_scopes_dropped": 0}

    # The list drops the region price and changes the geo one.
    prices[ident].pop(("cloud_region", "us-gov-west-1"))
    prices[ident][("geo", "us")] = {"input_cost_per_token": 3.4e-06}
    last = apply_prices(registry_db, {ident}, prices, date(2026, 9, 29))
    registry_db.commit()
    assert last == {"price_scopes": 2, "price_changes": 1, "price_scopes_dropped": 1}
    scopes = {(p.scope_kind, p.scope) for p in registry_db.query(DBRegistryModelPrice).filter_by(model_id=model.id)}
    assert scopes == {("base", ""), ("geo", "us")}


def test_removed_model_keeps_its_prices(registry_db):
    model = _model(registry_db)
    ident = ("bedrock", "anthropic.claude-x-v1:0")
    apply_prices(registry_db, {ident}, parse_prices(LIST, {"bedrock"}), date(2026, 9, 27))
    registry_db.commit()

    apply_prices(registry_db, set(), {}, date(2026, 9, 28))
    registry_db.commit()

    assert registry_db.query(DBRegistryModelPrice).filter_by(model_id=model.id).count() == 3


def test_pick_price_follows_litellm_order(registry_db):
    model = _model(registry_db)
    apply_prices(registry_db, {("bedrock", "anthropic.claude-x-v1:0")}, parse_prices(LIST, {"bedrock"}), date(2026, 9, 27))
    registry_db.commit()

    def pick(deployed, region):
        row = pick_price(registry_db, model.id, "bedrock", deployed, region)
        return row and (row.scope_kind, row.scope)

    # A geo id is billed at its geo price, even in a region with its own price.
    assert pick("bedrock/us.anthropic.claude-x-v1:0", "us-gov-west-1") == ("geo", "us")
    # An in-region id gets the region price when there is one, else base.
    assert pick("bedrock/anthropic.claude-x-v1:0", "us-gov-west-1") == ("cloud_region", "us-gov-west-1")
    assert pick("bedrock/anthropic.claude-x-v1:0", "us-east-1") == ("base", "")
    # A geo with no price of its own falls back to base.
    assert pick("bedrock/eu.anthropic.claude-x-v1:0", None) == ("base", "")
    assert pick_price(registry_db, _model(registry_db, model_id="unpriced").id, "bedrock", None, None) is None


def test_proxy_price_is_kept_until_the_list_prices_the_model(registry_db):
    model = _model(registry_db, model_id="amazon.new")
    registry_db.add(
        DBRegistryModelPrice(
            model_id=model.id, scope_kind="base", scope="", prices={"input_cost_per_token": 1e-06},
            source="proxy", last_seen=date(2026, 9, 27),
        )
    )
    registry_db.commit()
    ident = ("bedrock", "amazon.new")

    # Not on the list: the proxy price stays.
    apply_prices(registry_db, set(), {}, date(2026, 9, 28))
    # On the list without a price of its own: still kept.
    apply_prices(registry_db, {ident}, {}, date(2026, 9, 28))
    registry_db.commit()
    row = registry_db.get(DBRegistryModelPrice, (model.id, "base", ""))
    assert row.source == "proxy" and row.prices == {"input_cost_per_token": 1e-06}

    # The list prices it: the list wins.
    apply_prices(registry_db, {ident}, {ident: {("base", ""): {"input_cost_per_token": 2e-06}}}, date(2026, 9, 29))
    registry_db.commit()
    row = registry_db.get(DBRegistryModelPrice, (model.id, "base", ""))
    assert row.source == "litellm" and row.prices == {"input_cost_per_token": 2e-06}


def test_price_fields_drop_non_finite_numbers_and_keep_objects():
    entry = {"input_cost_per_token": float("inf"), "output_cost_per_token": 1e-06,
             "search_context_cost_per_query": {"search_context_size_low": 0.01}, "cache_cost": None}
    assert price_fields(entry) == {"output_cost_per_token": 1e-06,
                                   "search_context_cost_per_query": {"search_context_size_low": 0.01}}
