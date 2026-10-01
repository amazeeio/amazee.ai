import asyncio
from datetime import UTC, datetime

from app.db.models import DBModel, DBModelRegion
from scripts import resync_model_info
from tests.conftest import TestingSessionLocal


def test_resync_continues_after_a_row_is_deleted(db, test_region, monkeypatch):
    models = [
        DBModel(model_id=f"model-{i}", display_name=f"Model {i}", provider="x", type="chat")
        for i in range(2)
    ]
    db.add_all(models)
    db.commit()
    db.add_all([DBModelRegion(model_id=m.id, region_id=test_region.id) for m in models])
    db.commit()
    first, second = models[0].id, models[1].id

    async def fake_sync(model_id, region_id):
        other = TestingSessionLocal()
        assoc = other.query(DBModelRegion).filter_by(model_id=model_id, region_id=region_id).one()
        if model_id == first:
            other.delete(assoc)
        else:
            assoc.sync_status = "synced"
            assoc.synced_at = datetime.now(UTC)
        other.commit()
        other.close()

    monkeypatch.setattr(resync_model_info, "SessionLocal", TestingSessionLocal)
    monkeypatch.setattr(resync_model_info, "sync_model_to_region_task", fake_sync)
    monkeypatch.setattr(resync_model_info, "catalog_manages", lambda name: True)

    assert asyncio.run(resync_model_info.main()) == 1
    db.expire_all()
    assert db.query(DBModelRegion).filter_by(model_id=second).one().sync_status == "synced"
