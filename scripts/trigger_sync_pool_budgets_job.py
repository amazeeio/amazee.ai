#!/usr/bin/env python3

import os
import sys
import asyncio
import logging

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.api import budgets
from app.core.locking import try_acquire_lock, release_lock

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def trigger_sync_pool_budgets_job():
    """Manually trigger the sync pool budgets job"""

    # Create database session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "sync_pool_budgets"

    try:
        logger.info("Starting manual sync pool budgets job trigger...")

        # Try to acquire the lock
        if try_acquire_lock(lock_name, db, lock_timeout=10):
            logger.info("Acquired sync_pool_budgets lock, executing job")
            try:
                result = await budgets.sync_pool_team_budgets(db)
                logger.info(
                    f"Pool budgets sync complete: {result['teams_updated']} teams updated"
                )
            except Exception as e:
                logger.error(f"Error in sync pool budgets job execution: {str(e)}")
                raise
            finally:
                # Always release the lock when done
                release_lock(lock_name, db)
                logger.info("Released sync_pool_budgets lock")
        else:
            logger.warning(
                "Another process has the sync_pool_budgets lock, cannot execute job"
            )
            logger.info("This is normal if the scheduled job is currently running")
            return False

    except Exception as e:
        logger.error(f"Error in sync pool budgets job trigger: {str(e)}")
        raise
    finally:
        db.close()

    return True


def main():
    """Main function to run the script"""
    try:
        logger.info("Triggering sync pool budgets job manually...")
        success = asyncio.run(trigger_sync_pool_budgets_job())

        if success:
            logger.info("✅ Sync pool budgets job completed successfully")
            sys.exit(0)
        else:
            logger.info(
                "⚠️  Sync pool budgets job could not be executed (lock held by another process)"
            )
            # Exiting with status 0 because this is a normal, expected condition (lock held)
            sys.exit(0)

    except Exception as e:
        logger.error(f"❌ Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
