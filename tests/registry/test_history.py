from datetime import UTC, date, datetime
from decimal import Decimal
from unittest.mock import patch

import pytest

from app.db.models import DBRegion
from app.registry.history import run_history
from app.registry.models import (
    DBRegistryModel,
    DBRegistryModelHistory,
    DBRegistryModelSupport,
    DBRegistryProvider,
)
from app.registry.support import run_support_check

TODAY = date(2026, 10, 10)


def _model(db, model_id, **fields):
    provider = db.query(DBRegistryProvider).filter_by(name="bedrock").first()
    if provider is None:
        provider = DBRegistryProvider(name="bedrock")
        db.add(provider)
        db.flush()
    row = DBRegistryModel(
        provider_id=provider.id, model_id=model_id, supports=[], prices={}, source="litellm",
        first_seen=TODAY, last_seen=TODAY, **fields,
    )
    db.add(row)
    db.flush()
    return row


def _support(db, model, region, **flags):
    row = DBRegistryModelSupport(
        model_id=model.id, region_id=region.id, checked_at=datetime.now(UTC),
        **{"priced": True, "supported": False, "region_available": None, **flags},
    )
    db.add(row)
    db.flush()
    return row


def _states(db, model):
    rows = db.query(DBRegistryModelHistory).filter_by(model_id=model.id).order_by(DBRegistryModelHistory.id)
    return [row.state for row in rows]


def test_first_run_records_every_model(registry_db, proxy_region):
    first = _model(
        registry_db, "anthropic.a-v1:0", status="deprecated", eol_date=date(2027, 1, 31),
        input_cost_per_token=Decimal("0.000003"), output_cost_per_token=None,
    )
    second = _model(registry_db, "anthropic.b-v1:0")
    _support(registry_db, first, proxy_region)
    registry_db.commit()

    assert run_history(registry_db) == {"models": 2, "recorded": 2, "first": 2}

    first_states, second_states = _states(registry_db, first), _states(registry_db, second)
    assert len(first_states) == len(second_states) == 1
    assert first_states[0] == {
        "status": "deprecated",
        "eol_date": "2027-01-31",
        "input_cost_per_token": 3e-06,
        "output_cost_per_token": None,
        "support": {str(proxy_region.id): {"priced": True, "supported": False, "region_available": None}},
    }
    assert second_states[0]["support"] == {}


def test_second_run_with_no_change_records_nothing(registry_db, proxy_region):
    _model(registry_db, "anthropic.m-v1:0")
    registry_db.commit()
    proxy_list = {"anthropic.m-v1:0": {"litellm_provider": "bedrock", "input_cost_per_token": 1e-06}}

    for _ in range(2):
        # The support check moves checked_at on every run, so this proves it is not in the state.
        with patch("app.registry.support.fetch_json", return_value=proxy_list):
            run_support_check(registry_db)
        stats = run_history(registry_db)

    assert stats == {"models": 1, "recorded": 0, "first": 0}
    assert registry_db.query(DBRegistryModelHistory).count() == 1


def test_decimal_price_read_back_from_json_is_no_change(registry_db):
    model = _model(
        registry_db, "anthropic.a-v1:0",
        input_cost_per_token=Decimal("0.000003"), output_cost_per_token=Decimal("0.000015"),
    )
    registry_db.commit()
    run_history(registry_db)
    registry_db.expire_all()

    assert run_history(registry_db)["recorded"] == 0
    price = _states(registry_db, model)[0]["input_cost_per_token"]
    assert isinstance(price, float) and price == 3e-06


@pytest.mark.parametrize(
    ("target", "field", "value", "expected"),
    [
        ("model", "status", "deprecated", "deprecated"),
        ("model", "eol_date", date(2027, 1, 31), "2027-01-31"),
        ("model", "input_cost_per_token", Decimal("0.000004"), 4e-06),
        ("support", "supported", True, True),
    ],
)
def test_each_tracked_change_records_one_row(registry_db, proxy_region, target, field, value, expected):
    model = _model(registry_db, "anthropic.a-v1:0", input_cost_per_token=Decimal("0.000003"))
    support = _support(registry_db, model, proxy_region)
    registry_db.commit()
    run_history(registry_db)

    setattr(model if target == "model" else support, field, value)
    registry_db.commit()
    stats = run_history(registry_db)

    assert stats["recorded"] == 1 and stats["first"] == 0
    states = _states(registry_db, model)
    assert len(states) == 2
    found = states[-1] if target == "model" else states[-1]["support"][str(proxy_region.id)]
    assert found[field] == expected

    # A third run must compare with the newest row, not the first one.
    assert run_history(registry_db)["recorded"] == 0
    assert len(_states(registry_db, model)) == 2


def test_support_row_for_a_second_proxy_is_a_change(registry_db, proxy_region):
    model = _model(registry_db, "anthropic.a-v1:0")
    _support(registry_db, model, proxy_region)
    registry_db.commit()
    run_history(registry_db)

    second = DBRegion(name="second", litellm_api_url="http://second:4000", litellm_api_key="sk-second", is_active=True)
    registry_db.add(second)
    registry_db.flush()
    _support(registry_db, model, second)
    registry_db.commit()

    assert run_history(registry_db)["recorded"] == 1
    assert set(_states(registry_db, model)[-1]["support"]) == {str(proxy_region.id), str(second.id)}


def test_daily_job_runs_history_last():
    from scripts import trigger_registry_daily_job

    assert trigger_registry_daily_job.STEPS[-1][1] is run_history
