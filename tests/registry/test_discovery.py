from datetime import date
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.registry import config
from app.registry.discovery import apply_model_list, parse_model_list, run_discovery
from app.registry.models import DBRegistryModel, DBRegistryProvider, DBRegistryRun

LIST = {
    "sample_spec": {"litellm_provider": "bedrock"},
    "us.anthropic.claude-x-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 2e-06},
    "anthropic.claude-x-v1:0": {
        "litellm_provider": "bedrock",
        "mode": "chat",
        "max_input_tokens": 200000,
        "max_tokens": 8192,
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": 1.5e-05,
        "supports_vision": True,
        "supports_reasoning": False,
        "deprecation_date": "2027-01-31",
    },
    "eu.amazon.only-geo-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 1e-06},
    "bedrock/us-east-1/anthropic.claude-x-v1:0": {"litellm_provider": "bedrock"},
    "gpt-4o": {"litellm_provider": "openai"},
    "azure/gpt-4o": {"litellm_provider": "azure"},
}


def test_parse_prefers_base_entry_and_filters_providers():
    models, variants = parse_model_list(LIST, {"bedrock", "azure"})

    assert set(models) == {
        ("bedrock", "anthropic.claude-x-v1:0"),
        ("bedrock", "amazon.only-geo-v1:0"),
        ("azure", "gpt-4o"),
    }
    assert variants == 1
    claude = models[("bedrock", "anthropic.claude-x-v1:0")]
    assert claude["input_cost_per_token"] == Decimal("3e-06")
    assert claude["max_output_tokens"] == 8192
    assert claude["supports"] == ["vision"]
    assert claude["eol_date"] == date(2027, 1, 31)
    # A model the list only has with a geo prefix still gets a row.
    assert models[("bedrock", "amazon.only-geo-v1:0")]["input_cost_per_token"] == Decimal("1e-06")


def _add_provider(db, name="bedrock"):
    provider = DBRegistryProvider(name=name)
    db.add(provider)
    db.flush()
    return provider


def _model(db, provider, model_id, source="litellm", status="active"):
    row = DBRegistryModel(
        provider_id=provider.id,
        model_id=model_id,
        supports=[],
        status=status,
        source=source,
        first_seen=date(2026, 1, 1),
        last_seen=date(2026, 1, 1),
    )
    db.add(row)
    db.flush()
    return row


def test_apply_inserts_updates_and_deprecates(registry_db):
    bedrock = _add_provider(registry_db)
    kept = _model(registry_db, bedrock, "kept")
    gone = _model(registry_db, bedrock, "gone")
    from_proxy = _model(registry_db, bedrock, "proxy-only", source="proxy")
    fields = parse_model_list({"kept": {"litellm_provider": "bedrock", "mode": "chat"}}, {"bedrock"})[0]
    fields[("bedrock", "new")] = fields[("bedrock", "kept")]

    stats = apply_model_list(registry_db, fields, date(2026, 9, 26))
    registry_db.commit()

    assert stats == {"listed": 2, "inserted": 1, "updated": 1, "deprecated": 1}
    assert kept.mode == "chat" and kept.last_seen == date(2026, 9, 26)
    assert gone.status == "deprecated"
    # Never on the list, so leaving it means nothing.
    assert from_proxy.status == "active"
    new = registry_db.query(DBRegistryModel).filter_by(model_id="new").one()
    assert new.first_seen == date(2026, 9, 26) and new.source == "litellm"

    again = apply_model_list(registry_db, fields, date(2026, 9, 27))
    assert again == {"listed": 2, "inserted": 0, "updated": 0, "deprecated": 0}


def test_apply_takes_over_proxy_model_and_revives_deprecated(registry_db):
    bedrock = _add_provider(registry_db)
    from_proxy = _model(registry_db, bedrock, "a", source="proxy")
    old = _model(registry_db, bedrock, "b", status="deprecated")
    listed = {("bedrock", "a"): {}, ("bedrock", "b"): {}}

    apply_model_list(registry_db, listed, date(2026, 9, 26))

    assert from_proxy.source == "litellm"
    assert old.status == "active" and old.first_seen == date(2026, 1, 1)


def test_apply_refuses_list_that_drops_most_models(registry_db):
    bedrock = _add_provider(registry_db)
    for i in range(4):
        _model(registry_db, bedrock, f"m{i}")

    with pytest.raises(RuntimeError, match="refusing"):
        apply_model_list(registry_db, {("bedrock", "m0"): {}}, date(2026, 9, 26))


def test_run_discovery_records_run(registry_db):
    _add_provider(registry_db)
    registry_db.commit()
    with patch("app.registry.discovery.fetch_model_list", return_value=LIST) as fetch:
        stats = run_discovery(registry_db, today=date(2026, 9, 26))

    fetch.assert_called_once_with(config.LITELLM_LIST_URL)
    assert stats["inserted"] == 2
    run = registry_db.query(DBRegistryRun).one()
    assert run.status == "ok" and run.finished_at is not None


def test_run_discovery_failed_fetch_writes_no_models(registry_db):
    _add_provider(registry_db)
    registry_db.commit()
    with patch("app.registry.discovery.fetch_model_list", side_effect=RuntimeError("boom")):
        with pytest.raises(RuntimeError):
            run_discovery(registry_db)

    assert registry_db.query(DBRegistryModel).count() == 0
    run = registry_db.query(DBRegistryRun).one()
    assert run.status == "failed" and run.error == "boom"


def test_guard_ignores_models_already_deprecated(registry_db):
    bedrock = _add_provider(registry_db)
    for i in range(4):
        _model(registry_db, bedrock, f"old{i}", status="deprecated")
    _model(registry_db, bedrock, "live")

    stats = apply_model_list(registry_db, {("bedrock", "live"): {}}, date(2026, 9, 26))

    assert stats["deprecated"] == 0


def test_updated_at_moves_only_on_a_real_change(registry_db):
    bedrock = _add_provider(registry_db)
    row = _model(registry_db, bedrock, "m")
    registry_db.commit()
    before = row.updated_at

    apply_model_list(registry_db, {("bedrock", "m"): {}}, date(2026, 9, 27))
    registry_db.commit()
    assert row.updated_at == before and row.last_seen == date(2026, 9, 27)

    apply_model_list(registry_db, {("bedrock", "m"): {"mode": "chat"}}, date(2026, 9, 28))
    registry_db.commit()
    assert row.updated_at > before
