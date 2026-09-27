from datetime import date
from unittest.mock import patch

import pytest

from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider, DBRegistryRun
from app.registry.models_dev import apply_models_dev, parse_models_dev, run_models_dev

PAYLOAD = {
    "amazon-bedrock": {
        "models": {
            "xai.grok-4.3": {"cost": {"input": 1.25, "output": 2.5, "cache_read": 0.2}},
            "us.xai.grok-4.3": {"cost": {"input": 1.375, "output": 2.75}},
            "no-cost": {"name": "x"},
            "amazon.nova-canvas-v1:0": {"cost": {"input": 0, "output": 40}, "modalities": {"output": ["image"]}},
        }
    },
    "google-vertex-anthropic": {"models": {"claude-sonnet-4-5@20250929": {"cost": {"input": 3, "output": 15}}}},
    "deepinfra": {"models": {"BAAI/bge-m3": {"cost": {"input": 0.01, "output": 0}}}},
    "openrouter": {"models": {"x/y": {"cost": {"input": 9, "output": 9}}}},
}


def test_parse_maps_providers_units_and_geo_scopes():
    prices = parse_models_dev(PAYLOAD)

    assert prices[("bedrock", "xai.grok-4.3")] == {
        ("base", ""): {
            "input_cost_per_token": 1.25e-06,
            "output_cost_per_token": 2.5e-06,
            "cache_read_input_token_cost": 2e-07,
        },
        # A geo id's price is the geo's, never the base.
        ("geo", "us"): {"input_cost_per_token": 1.375e-06, "output_cost_per_token": 2.75e-06},
    }
    assert ("vertex_ai", "claude-sonnet-4-5@20250929") in prices
    assert not any(p == "openrouter" for p, _ in prices)
    assert ("bedrock", "no-cost") not in prices
    assert ("bedrock", "amazon.nova-canvas-v1:0") not in prices


@pytest.mark.parametrize("bad", [[], {"openrouter": {"models": {}}}])
def test_parse_rejects_payload_without_our_prices(bad):
    with pytest.raises(ValueError):
        parse_models_dev(bad)


def _model(db, provider_name, model_id, mode="chat", prices=None, source="proxy"):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, mode=mode, supports=[], status="active",
        source=source, prices=prices or {}, first_seen=date(2026, 9, 1), last_seen=date(2026, 9, 1),
    )
    db.add(row)
    db.flush()
    return row


def test_fills_only_models_without_any_price(registry_db):
    # Proxy-only models often have no mode.
    grok = _model(registry_db, "bedrock", "xai.grok-4.3", mode=None)
    embed = _model(registry_db, "deepinfra", "BAAI/bge-m3", mode="embedding")
    priced = _model(registry_db, "vertex_ai", "claude-sonnet-4-5@20250929", source="litellm")
    registry_db.add(
        DBRegistryModelPrice(
            model_id=priced.id, scope_kind="base", scope="", prices={"input_cost_per_token": 3e-06},
            source="litellm", last_seen=date(2026, 9, 27),
        )
    )
    old = _model(registry_db, "bedrock", "us.gone", prices={"input_cost_per_token": 1e-06}, source="litellm")
    registry_db.commit()

    stats = apply_models_dev(registry_db, parse_models_dev(PAYLOAD), date(2026, 9, 27))
    registry_db.commit()

    assert stats == {"filled": 1, "scopes": 2, "handed_back": 0, "skipped_mode": 1, "unmatched": 0}
    scopes = {(p.scope_kind, p.scope): p.source for p in registry_db.query(DBRegistryModelPrice).filter_by(model_id=grok.id)}
    assert scopes == {("base", ""): "models_dev", ("geo", "us"): "models_dev"}
    assert float(grok.input_cost_per_token) == pytest.approx(1.25e-06)
    # Never replaces another source's price, and skips non-text models.
    assert registry_db.get(DBRegistryModelPrice, (priced.id, "base", "")).source == "litellm"
    assert registry_db.query(DBRegistryModelPrice).filter_by(model_id=embed.id).count() == 0
    assert old.prices == {"input_cost_per_token": 1e-06}

    again = apply_models_dev(registry_db, parse_models_dev(PAYLOAD), date(2026, 9, 28))
    assert again["filled"] == 1 and again["scopes"] == 2


def test_steps_aside_when_litellm_prices_the_model(registry_db):
    grok = _model(registry_db, "bedrock", "xai.grok-4.3")
    apply_models_dev(registry_db, parse_models_dev(PAYLOAD), date(2026, 9, 27))
    registry_db.commit()
    # LiteLLM's list now prices it: its base row replaces ours.
    base = registry_db.get(DBRegistryModelPrice, (grok.id, "base", ""))
    base.source, base.prices = "litellm", {"input_cost_per_token": 1e-06}
    registry_db.commit()

    stats = apply_models_dev(registry_db, parse_models_dev(PAYLOAD), date(2026, 9, 28))
    registry_db.commit()

    assert stats["handed_back"] == 1
    assert {p.source for p in registry_db.query(DBRegistryModelPrice).filter_by(model_id=grok.id)} == {"litellm"}


def test_run_records_failure(registry_db):
    with patch("app.registry.models_dev.fetch_model_list", return_value={"openrouter": {}}):
        with pytest.raises(ValueError):
            run_models_dev(registry_db)
    assert registry_db.query(DBRegistryRun).one().status == "failed"
