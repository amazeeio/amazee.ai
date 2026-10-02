from datetime import date
from types import SimpleNamespace

from app.registry import config
from app.registry.eol import apply_eol, run_eol
from app.registry.models import DBRegistryModel, DBRegistryModelLifecycle, DBRegistryProvider, DBRegistryRun

TODAY = date(2026, 9, 27)
X = date(2026, 12, 1)
Y = date(2027, 6, 1)


def _model(db, provider_name, model_id, eol_date=None):
    provider = db.query(DBRegistryProvider).filter_by(name=provider_name).first()
    if provider is None:
        provider = DBRegistryProvider(name=provider_name)
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, supports=[], status="active", source="litellm",
        prices={}, eol_date=eol_date, first_seen=TODAY, last_seen=TODAY,
    )
    db.add(row)
    db.flush()
    return row


def _life(db, source, eol_date, provider="bedrock", model_id="m"):
    db.add(DBRegistryModelLifecycle(
        provider=provider, model_id=model_id, source=source, eol_date=eol_date, last_seen=TODAY,
    ))
    db.flush()


def test_priority_wins(registry_db):
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "litellm", Y)
    _life(registry_db, "bedrock_community", X)

    stats = apply_eol(registry_db)

    assert model.eol_date == X
    assert stats["changed"] == 1 and stats["won"] == {"bedrock_community": 1}


def test_falls_back_when_the_winner_has_no_date(registry_db):
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "litellm", Y)
    _life(registry_db, "bedrock_community", None)

    apply_eol(registry_db)

    assert model.eol_date == Y


def test_disabled_plugin_does_not_count(registry_db, monkeypatch):
    monkeypatch.setattr(config, "DISABLED_PLUGINS", {"bedrock_community"})
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "litellm", Y)
    _life(registry_db, "bedrock_community", X)

    apply_eol(registry_db)

    assert model.eol_date == Y


def test_unknown_source_does_not_count(registry_db):
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "nobody", X)

    apply_eol(registry_db)

    assert model.eol_date is None


def test_tie_goes_to_the_lowest_source_name(registry_db, monkeypatch):
    plugins = {
        "b_src": SimpleNamespace(PRIORITY={"eol_date": 90}),
        "a_src": SimpleNamespace(PRIORITY={"eol_date": 90}),
    }
    monkeypatch.setattr("app.registry.eol.load_plugins", lambda: plugins)
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "b_src", Y)
    _life(registry_db, "a_src", X)

    apply_eol(registry_db)

    assert model.eol_date == X


def test_placeholder_date_is_dropped(registry_db):
    model = _model(registry_db, "bedrock", "m")
    alone = _model(registry_db, "bedrock", "alone")
    _life(registry_db, "bedrock_community", date(2090, 1, 1))
    _life(registry_db, "litellm", Y)
    _life(registry_db, "litellm", date(2099, 12, 31), model_id="alone")

    apply_eol(registry_db)

    assert model.eol_date == Y
    assert alone.eol_date is None


def test_no_source_gives_null(registry_db):
    model = _model(registry_db, "bedrock", "m", eol_date=X)

    stats = apply_eol(registry_db)

    assert model.eol_date is None and stats["changed"] == 1


def test_second_run_changes_nothing(registry_db):
    model = _model(registry_db, "bedrock", "m")
    _life(registry_db, "bedrock_community", X)
    registry_db.commit()

    first = run_eol(registry_db)
    registry_db.commit()
    updated_at = model.updated_at
    second = run_eol(registry_db)
    registry_db.commit()

    assert first["changed"] == 1 and second["changed"] == 0
    registry_db.refresh(model)
    assert model.eol_date == X and model.updated_at == updated_at
    runs = registry_db.query(DBRegistryRun).filter_by(step="eol").all()
    assert len(runs) == 2 and all(run.status == "ok" for run in runs)
