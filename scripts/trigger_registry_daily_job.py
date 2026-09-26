#!/usr/bin/env python3
"""Daily registry job: refresh registry_models from LiteLLM's model list."""

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker

from app.core.locking import release_lock, try_acquire_lock
from app.db.database import engine
from app.registry.discovery import run_discovery

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

LOCK_NAME = "registry_daily"


def main():
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        # 30 minutes outlasts a slow run and still expires long before the
        # next day, so one crashed run cannot block the next.
        if not try_acquire_lock(LOCK_NAME, db, lock_timeout=30):
            logger.warning("Another process holds the %s lock, skipping", LOCK_NAME)
            return
        try:
            logger.info("Registry model list: %s", run_discovery(db))
        finally:
            db.rollback()  # a failed run can leave the session unusable for the lock release
            release_lock(LOCK_NAME, db)
    except Exception:
        logger.exception("Registry daily job failed")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
