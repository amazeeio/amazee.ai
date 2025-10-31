#!/usr/bin/env python3

import os
import sys
import asyncio
import logging
from datetime import UTC

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), '..')))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.core.worker import hard_delete_expired_teams
from app.core.locking import try_acquire_lock, release_lock

# Configure logging
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)

logger = logging.getLogger(__name__)

async def trigger_hard_delete_job():
    """Manually trigger the hard delete job in the background scheduler thread"""

    # Create database session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "hard_delete_teams"

    try:
        logger.info("Starting manual hard delete job trigger...")

        # Try to acquire the lock
        if try_acquire_lock(lock_name, db, lock_timeout=10):
            logger.info("Acquired hard_delete_teams lock, executing hard delete job")
            try:
                await hard_delete_expired_teams(db)
                logger.info("Hard delete job completed successfully")
            except Exception as e:
                logger.error(f"Error in hard delete job execution: {str(e)}")
                raise
            finally:
                # Always release the lock when done
                release_lock(lock_name, db)
                logger.info("Released hard_delete_teams lock")
        else:
            logger.warning("Another process has the hard_delete_teams lock, cannot execute hard delete job")
            logger.info("This is normal if the scheduled job is currently running")
            return False

    except Exception as e:
        logger.error(f"Error in hard delete job trigger: {str(e)}")
        # Try to release lock in case of error
        try:
            release_lock(lock_name, db)
            logger.info("Released lock after error")
        except Exception as release_error:
            logger.error(f"Error releasing lock: {str(release_error)}")
        raise
    finally:
        db.close()

    return True

def main():
    """Main function to run the script"""
    try:
        logger.info("Triggering hard delete job manually...")
        success = asyncio.run(trigger_hard_delete_job())

        if success:
            logger.info("✅ Hard delete job completed successfully")
            sys.exit(0)
        else:
            logger.info("⚠️  Hard delete job could not be executed (lock held by another process)")
            sys.exit(1)

    except Exception as e:
        logger.error(f"❌ Script failed: {str(e)}")
        sys.exit(1)

if __name__ == "__main__":
    main()

