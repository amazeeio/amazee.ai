#!/usr/bin/env python3
"""Cron entry point for the trial-key reaper (``reap_trial_keys``).

Deletes abandoned anonymous-trial keys and the LiteLLM keys, Postgres databases
and user rows they hold. Skips the live trial region. See
``app.core.trial_cleanup`` for the safety rules.

For a one-off, region-targeted clean-up use ``scripts/cleanup_trial_keys.py``
instead — it takes filters and a dry run.
"""

import asyncio
import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker  # noqa: E402

from app.core.locking import release_lock, try_acquire_lock  # noqa: E402
from app.core.worker import reap_trial_keys  # noqa: E402
from app.db.database import engine  # noqa: E402

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def trigger_trial_reaper_job():
    """Run the trial reaper under a lock."""

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "reap_trial_keys"

    try:
        logger.info("Starting trial reaper job...")

        if try_acquire_lock(lock_name, db, lock_timeout=10):
            logger.info("Acquired reap_trial_keys lock, executing job")
            try:
                await reap_trial_keys(db)
                logger.info("Trial reaper job completed successfully")
            except Exception as e:
                logger.error(f"Error in trial reaper job execution: {str(e)}")
                raise
            finally:
                # Always release the lock when done
                release_lock(lock_name, db)
                logger.info("Released reap_trial_keys lock")
        else:
            logger.warning(
                "Another process has the reap_trial_keys lock, cannot execute job"
            )
            return False

    except Exception as e:
        # Lock release is handled by the inner finally; releasing again here
        # could free a lock another process has since acquired.
        logger.error(f"Error in trial reaper job trigger: {str(e)}")
        raise
    finally:
        db.close()

    return True


def main():
    """Main function to run the script"""
    try:
        logger.info("Triggering trial reaper job...")
        success = asyncio.run(trigger_trial_reaper_job())

        if success:
            logger.info("✅ Trial reaper job completed successfully")
            sys.exit(0)
        else:
            logger.info(
                "⚠️  Trial reaper job could not be executed (lock held by another process)"
            )
            sys.exit(1)

    except Exception as e:
        logger.error(f"❌ Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
