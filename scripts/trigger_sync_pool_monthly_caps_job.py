#!/usr/bin/env python3

import asyncio
import logging
import os
import sys

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker

from app.api import budgets
from app.core.locking import release_lock, try_acquire_lock
from app.db.database import engine

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def trigger_sync_pool_monthly_caps_job():
    """Manually trigger the sync pool monthly caps job."""
    # Create database session
    session_local = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = session_local()

    lock_name = "sync_pool_monthly_caps"

    try:
        logger.info("Starting manual sync pool monthly caps job trigger...")

        # Try to acquire the lock
        if try_acquire_lock(lock_name, db, lock_timeout=10):
            logger.info("Acquired sync_pool_monthly_caps lock, executing job")
            try:
                result = await budgets.sync_pool_team_monthly_caps(db)
                logger.info(
                    "Pool monthly caps sync complete: %s teams updated",
                    result["teams_updated"],
                )
            except Exception as e:
                logger.error(
                    "Error in sync pool monthly caps job execution: %s", str(e)
                )
                raise
            finally:
                # Always release the lock when done
                release_lock(lock_name, db)
                logger.info("Released sync_pool_monthly_caps lock")
        else:
            logger.warning(
                "Another process has the sync_pool_monthly_caps lock, cannot execute job"
            )
            logger.info("This is normal if the scheduled job is currently running")
            return False

    except Exception as e:
        logger.error("Error in sync pool monthly caps job trigger: %s", str(e))
        raise
    finally:
        db.close()

    return True


def main():
    """Main function to run the script."""
    try:
        logger.info("Triggering sync pool monthly caps job manually...")
        success = asyncio.run(trigger_sync_pool_monthly_caps_job())

        if success:
            logger.info("Sync pool monthly caps job completed successfully")
            sys.exit(0)

        logger.info(
            "Sync pool monthly caps job could not be executed (lock held by another process)"
        )
        # Exiting with status 0 because this is a normal, expected condition (lock held)
        sys.exit(0)

    except Exception as e:
        logger.error("Script failed: %s", str(e))
        sys.exit(1)


if __name__ == "__main__":
    main()
