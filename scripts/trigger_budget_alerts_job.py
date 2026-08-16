#!/usr/bin/env python3

import os
import sys
import asyncio
import logging

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.core.budget_alert_service import monitor_budget_thresholds
from app.core.locking import try_acquire_lock, release_lock

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def trigger_budget_alerts_job():
    """Run the budget threshold sweep, guarded by the shared advisory lock."""

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "budget_alerts"

    try:
        logger.info("Starting budget threshold alert job...")

        # The lock timeout is deliberately shorter than for monitor_teams: this
        # job runs every few minutes, so a stale lock must not block many ticks.
        if try_acquire_lock(lock_name, db, lock_timeout=5):
            logger.info("Acquired budget_alerts lock, executing sweep")
            try:
                totals = await monitor_budget_thresholds(db)
                logger.info("Budget threshold sweep completed: %s", totals)
            except Exception as e:
                logger.error(f"Error in budget threshold sweep: {str(e)}")
                raise
            finally:
                release_lock(lock_name, db)
                logger.info("Released budget_alerts lock")
        else:
            logger.warning(
                "Another process holds the budget_alerts lock, skipping this tick"
            )
            return False

    except Exception as e:
        logger.error(f"Error in budget threshold alert job: {str(e)}")
        raise
    finally:
        db.close()

    return True


def main():
    """Main function to run the script"""
    try:
        success = asyncio.run(trigger_budget_alerts_job())

        if success:
            logger.info("✅ Budget threshold sweep completed successfully")
            sys.exit(0)
        else:
            logger.info("⚠️  Sweep skipped (lock held by another process)")
            # Skipping is expected while a previous tick is still running.
            sys.exit(0)

    except Exception as e:
        logger.error(f"❌ Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
