from datetime import date

import pytest

from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider
from app.registry.plugins.parsers import models_dev
from app.registry.plugins.runner import apply_plugin, validate

TODAY = date(2026, 9, 27)
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


def test_transform_maps_providers_units_and_geo_scopes():
    out = models_dev.transform(PAYLOAD)
    models = {(m["provider"], m["model_id"]): m for m in out["models"]}

    assert models[("bedrock", "xai.grok-4.3")]["prices"] == {
        "base": {"input_cost_per_token": 1.25e-06, "output_cost_per_token": 2.5e-06, "cache_read_input_token_cost": 2e-07},
        # A geo id's price is the geo's, never the base.
        "geo:us": {"input_cost_per_token": 1.375e-06, "output_cost_per_token": 2.75e-06},
    }
    assert ("vertex_ai", "claude-sonnet-4-5@20250929") in models
    assert not any(p == "openrouter" for p, _ in models)
    assert ("bedrock", "no-cost") not in models
    assert ("bedrock", "amazon.nova-canvas-v1:0") not in models
    assert len(validate(out, "models_dev")) == len(out["models"])


@pytest.mark.parametrize("bad", [[], {"openrouter": {"models": {}}}])
def test_transform_rejects_payload_without_our_prices(bad):
    with pytest.raises(ValueError):
        models_dev.transform(bad)


def _model(db, provider_name, model_id, mode=None, prices=None, source="proxy"):
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


def _run(db):
    records = validate(models_dev.transform(PAYLOAD), "models_dev")
    stats = apply_plugin(db, "models_dev", "fill", records, TODAY, models_dev.FILL_MODES)
    db.commit()
    return stats


def test_fills_only_unpriced_text_models(registry_db):
    grok = _model(registry_db, "bedrock", "xai.grok-4.3")  # proxy-only, no mode
    embed = _model(registry_db, "deepinfra", "BAAI/bge-m3", mode="embedding")
    priced = _model(registry_db, "vertex_ai", "claude-sonnet-4-5@20250929", source="litellm")
    registry_db.add(DBRegistryModelPrice(model_id=priced.id, scope_kind="base", scope="",
                                         prices={"input_cost_per_token": 3e-06}, source="litellm", last_seen=TODAY))
    old = _model(registry_db, "bedrock", "us.gone", prices={"input_cost_per_token": 1e-06}, source="litellm")
    registry_db.commit()

    stats = _run(registry_db)

    assert stats["priced"] == 1
    scopes = {(p.scope_kind, p.scope): p.source for p in registry_db.query(DBRegistryModelPrice).filter_by(model_id=grok.id)}
    assert scopes == {("base", ""): "models_dev", ("geo", "us"): "models_dev"}
    assert float(grok.input_cost_per_token) == pytest.approx(1.25e-06)
    assert registry_db.get(DBRegistryModelPrice, (priced.id, "base", "")).source == "litellm"
    assert registry_db.query(DBRegistryModelPrice).filter_by(model_id=embed.id).count() == 0
    assert old.prices == {"input_cost_per_token": 1e-06}
    assert _run(registry_db)["price_changes"] == 0


def test_steps_aside_when_litellm_prices_the_model(registry_db):
    grok = _model(registry_db, "bedrock", "xai.grok-4.3")
    _run(registry_db)
    base = registry_db.get(DBRegistryModelPrice, (grok.id, "base", ""))
    base.source, base.prices = "litellm", {"input_cost_per_token": 1e-06}
    registry_db.commit()

    _run(registry_db)

    assert {p.source for p in registry_db.query(DBRegistryModelPrice).filter_by(model_id=grok.id)} == {"litellm"}
