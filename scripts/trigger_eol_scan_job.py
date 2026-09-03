#!/usr/bin/env python3

import os
import sys
import asyncio
import logging

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.core.locking import try_acquire_lock, release_lock
from app.services.model_eol import scan_models_for_eol

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)


async def trigger_eol_scan_job():
    """Resolve model EOL dates from upstream and send today's snapshot."""

    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "model_eol_scan"

    try:
        logger.info("Starting model EOL scan...")

        # try_acquire_lock takes minutes. A daily job can afford a long
        # window: 30 minutes covers a slow region fan-out and still expires
        # long before the next run, so one crashed tick cannot block the next.
        if try_acquire_lock(lock_name, db, lock_timeout=30):
            logger.info("Acquired model_eol_scan lock, executing scan")
            try:
                totals = await scan_models_for_eol(db)
                logger.info("Model EOL scan completed: %s", totals)
            except Exception as e:
                logger.error(f"Error in model EOL scan: {str(e)}")
                raise
            finally:
                release_lock(lock_name, db)
                logger.info("Released model_eol_scan lock")
        else:
            logger.warning(
                "Another process holds the model_eol_scan lock, skipping this tick"
            )
            return False

    except Exception as e:
        logger.error(f"Error in model EOL scan job: {str(e)}")
        raise
    finally:
        db.close()

    return True


def main():
    """Main function to run the script"""
    try:
        success = asyncio.run(trigger_eol_scan_job())

        if success:
            logger.info("✅ Model EOL scan completed successfully")
            sys.exit(0)
        else:
            logger.info("⚠️  Scan skipped (lock held by another process)")
            sys.exit(0)

    except Exception as e:
        logger.error(f"❌ Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
