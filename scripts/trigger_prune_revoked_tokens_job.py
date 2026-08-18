#!/usr/bin/env python3

import os
import sys
import logging

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.core.locking import try_acquire_lock, release_lock
from app.services.token_revocation import prune_revoked_tokens

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

LOCK_NAME = "prune_revoked_tokens"


def main():
    """Drop denylist rows whose token has expired.

    An expired token already fails validation, so its row only makes the table
    grow. Runs daily via the Lagoon cron, under the shared advisory lock so
    overlapping runs do not clobber each other.
    """
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()
    try:
        if not try_acquire_lock(LOCK_NAME, db, lock_timeout=10):
            logger.warning(
                "Another process holds the %s lock; skipping this run", LOCK_NAME
            )
            sys.exit(0)
        try:
            logger.info("Pruning expired revoked_tokens...")
            deleted = prune_revoked_tokens(db)
            logger.info("✅ Pruned %d revoked_tokens", deleted)
        finally:
            release_lock(LOCK_NAME, db)
    except Exception as e:  # noqa: BLE001
        logger.error("❌ revoked_tokens prune failed: %s", str(e))
        sys.exit(1)
    finally:
        db.close()

    sys.exit(0)


if __name__ == "__main__":
    main()
