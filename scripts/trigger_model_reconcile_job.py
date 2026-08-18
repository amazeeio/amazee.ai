#!/usr/bin/env python3
"""Cron entry point: repair drift between the model catalog's desired state
and each catalog-managed region's LiteLLM proxy.

'synced' in the DB only means a sync once succeeded — models can be deleted
behind our back (proxy admin UI, DB restores, cluster rollouts). This sweep
re-checks reality every few minutes and re-runs the ordinary sync task for
anything missing, stray, or with a drifted alias map.
"""

import os
import sys
import asyncio
import logging

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.services.model_sync import reconcile_all_managed_regions
from app.core.locking import try_acquire_lock, release_lock

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def trigger_model_reconcile_job():
    """Run the drift sweep, guarded by the shared advisory lock."""
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "model_reconcile"

    try:
        logger.info("Starting model drift reconcile job...")

        # Short timeout: this job runs every few minutes, so a stale lock must
        # not block many ticks.
        if try_acquire_lock(lock_name, db, lock_timeout=5):
            logger.info("Acquired model_reconcile lock, executing sweep")
            try:
                results = await reconcile_all_managed_regions(db)
                logger.info(f"Model reconcile finished: {results}")
            finally:
                release_lock(lock_name, db)
        else:
            logger.info("Another model_reconcile run holds the lock; skipping")
    finally:
        db.close()


if __name__ == "__main__":
    asyncio.run(trigger_model_reconcile_job())
