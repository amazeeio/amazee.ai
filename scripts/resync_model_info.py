#!/usr/bin/env python3
"""One-time push of stored model_info and access groups to every active
deployment in every catalog-managed region.

The reconcile diff compares litellm_params only, and existing deployments
never received model_info through the old update endpoint, so reconcile alone
never repairs them. Safe to run more than once: the sync task is idempotent.
"""

import os
import sys
import asyncio
import logging
from collections import Counter

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from app.core.config import catalog_manages
from app.db.database import SessionLocal
from app.db.models import DBModel, DBModelRegion, DBRegion
from app.services.model_sync import sync_model_to_region_task

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def main() -> int:
    db = SessionLocal()
    try:
        # Aliases carry no model_info, and their sync rewrites the region alias map.
        rows = (
            db.query(DBModelRegion, DBRegion.name)
            .join(DBModel, DBModel.id == DBModelRegion.model_id)
            .join(DBRegion, DBRegion.id == DBModelRegion.region_id)
            .filter(
                DBModelRegion.is_active.is_(True),
                DBModel.is_active_globally.is_(True),
                DBModel.deleted_at.is_(None),
                DBModel.is_alias.is_(False),
                DBRegion.is_active.is_(True),
            )
            .order_by(DBRegion.name, DBModelRegion.model_id)
            .all()
        )
        # Take the ids now: after a rollback, reading them would lazy-load and
        # hold a transaction open while the sync runs.
        rows = [
            (assoc, assoc.model_id, assoc.region_id, name)
            for assoc, name in rows
            if catalog_manages(name)
        ]
        db.rollback()
        logger.info(f"Resyncing {len(rows)} model-region rows")

        counts: Counter = Counter()
        failed = []
        for assoc, model_id, region_id, region_name in rows:
            await sync_model_to_region_task(model_id, region_id)
            db.refresh(assoc)
            status, error = assoc.sync_status, assoc.sync_error
            # End the read so the connection is not idle in a transaction for the whole run.
            db.rollback()
            counts[status] += 1
            if status == "failed":
                failed.append((model_id, region_name, error))

        print(f"total={len(rows)} " + " ".join(f"{k}={v}" for k, v in sorted(counts.items())))
        for model_id, region_name, error in failed:
            print(f"FAILED {model_id} / {region_name} / {error}")
        return 1 if failed else 0
    finally:
        db.close()


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
