from datetime import date
from types import SimpleNamespace

from app.registry import config
from app.registry.discovery import apply_model_list
from app.registry.eol import apply_eol, run_eol
from app.registry.models import DBRegistryModel, DBRegistryModelLifecycle, DBRegistryProvider, DBRegistryRun
from app.registry.plugins.runner import apply_plugin, validate
from app.registry.status import apply_status, run_status
from app.registry.support import run_support_check

TODAY = date(2026, 9, 27)


def _model(db, provider_name, model_id, status="active"):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, supports=[], status=status, source="litellm",
        prices={}, first_seen=TODAY, last_seen=TODAY,
    )
    db.add(row)
    db.flush()
    return row


def _life(db, source, status, provider="bedrock", model_id="m"):
    row = DBRegistryModelLifecycle(
        provider=provider, model_id=model_id, source=source, status=status, last_seen=TODAY,
    )
    db.add(row)
    db.flush()
    return row


def _plugins(monkeypatch, **status_priority):
    plugins = {name: SimpleNamespace(PRIORITY={"status": p}) for name, p in status_priority.items()}
    monkeypatch.setattr("app.registry.eol.load_plugins", lambda: plugins)


def test_legacy_deprecates_and_active_restores(registry_db):
    model = _model(registry_db, "bedrock", "m")
    life = _life(registry_db, "bedrock_community", "LEGACY")

    stats = apply_status(registry_db)

    assert model.status == "deprecated"
    assert stats["changed"] == 1 and stats["won"] == {"bedrock_community": 1}

    life.status = "active"
    registry_db.flush()
    apply_status(registry_db)

    assert model.status == "active"


def test_higher_priority_wins(registry_db, monkeypatch):
    # The names sort opposite to the priorities, so only the priority can pick the winner.
    _plugins(monkeypatch, z_high=90, a_low=10)
    one = _model(registry_db, "bedrock", "one")
    two = _model(registry_db, "bedrock", "two")
    _life(registry_db, "z_high", "LEGACY", model_id="one")
    _life(registry_db, "a_low", "ACTIVE", model_id="one")
    # Text with no opinion must not block a lower source.
    _life(registry_db, "z_high", "UNKNOWN", model_id="two")
    _life(registry_db, "a_low", "LEGACY", model_id="two")

    apply_status(registry_db)

    assert one.status == "deprecated"
    assert two.status == "deprecated"


def test_tie_goes_to_the_lowest_source_name(registry_db, monkeypatch):
    _plugins(monkeypatch, b_src=90, a_src=90)
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "a_src", "LEGACY")
    _life(registry_db, "b_src", "ACTIVE")

    apply_status(registry_db)

    assert model.status == "deprecated"


def test_disabled_plugin_does_not_count(registry_db, monkeypatch):
    monkeypatch.setattr(config, "DISABLED_PLUGINS", {"bedrock_community"})
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "bedrock_community", "LEGACY")

    stats = apply_status(registry_db)

    assert model.status == "active" and stats["changed"] == 0


def test_no_opinion_restores_active(registry_db):
    unknown = _model(registry_db, "bedrock", "m", status="deprecated")
    silent = _model(registry_db, "bedrock", "silent", status="deprecated")
    _life(registry_db, "bedrock_community", "UNKNOWN")

    stats = apply_status(registry_db)

    assert unknown.status == "active" and silent.status == "active"
    assert stats["changed"] == 2


def test_removed_model_stays_removed(registry_db):
    model = _model(registry_db, "bedrock", "m", status="removed")
    _life(registry_db, "bedrock_community", "LEGACY")

    stats = apply_status(registry_db)

    assert model.status == "removed" and stats["changed"] == 0


def test_second_run_changes_nothing(registry_db):
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "bedrock_community", "LEGACY")
    registry_db.commit()

    first = run_status(registry_db)
    registry_db.commit()
    updated_at = model.updated_at
    second = run_status(registry_db)
    registry_db.commit()

    assert first["changed"] == 1 and second["changed"] == 0
    registry_db.refresh(model)
    assert model.status == "deprecated" and model.updated_at == updated_at
    runs = registry_db.query(DBRegistryRun).filter_by(step="status").all()
    assert len(runs) == 2 and all(run.status == "ok" for run in runs)


def test_list_plugin_eol_and_status_twice_change_nothing_the_second_time(registry_db):
    model = _model(registry_db, "bedrock", "m")
    registry_db.commit()
    plugin_out = {"schema": 1, "source": "bedrock_community", "models": [
        {"provider": "bedrock", "model_id": "m", "lifecycle": {"status": "LEGACY"}},
    ]}

    listed, statuses = [], []
    for _ in range(2):
        # Commit after each step, as the daily job does through record_run.
        listed.append(apply_model_list(registry_db, {("bedrock", "m"): {}}, TODAY))
        registry_db.commit()
        apply_plugin(registry_db, "bedrock_community", "fill", validate(plugin_out, "bedrock_community"), TODAY)
        registry_db.commit()
        apply_eol(registry_db)
        registry_db.commit()
        statuses.append(apply_status(registry_db))
        registry_db.commit()

    assert statuses[0]["changed"] == 1
    assert listed[1]["updated"] == 0 and statuses[1]["changed"] == 0
    assert model.status == "deprecated"


def test_daily_job_runs_status_after_eol_and_before_the_support_check():
    from scripts import trigger_registry_daily_job

    steps = [step for _, step in trigger_registry_daily_job.STEPS]
    assert steps.index(run_eol) < steps.index(run_status) < steps.index(run_support_check)
