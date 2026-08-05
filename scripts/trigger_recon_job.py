#!/usr/bin/env python3

import os
import sys
import asyncio
import logging

# Add the parent directory to the Python path
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker
from app.db.database import engine
from app.core.worker import monitor_teams
from app.core.locking import try_acquire_lock, release_lock

# Configure logging
logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)

logger = logging.getLogger(__name__)

# EX_TEMPFAIL. Signals "did not run, try later" - distinct from success (0) and
# from a hard failure (1) so an overrunning previous run can be alerted on.
EXIT_LOCK_CONTENTION = 75


async def trigger_recon_job():
    """Manually trigger the recon job (monitor_teams) in the background scheduler thread"""

    # Create database session
    SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
    db = SessionLocal()

    lock_name = "monitor_teams"

    try:
        logger.info("Starting manual recon job trigger...")

        # Try to acquire the lock
        if try_acquire_lock(lock_name, db, lock_timeout=10):
            logger.info("Acquired monitor_teams lock, executing recon job")
            try:
                await monitor_teams(db)
                logger.info("Recon job completed successfully")
            except Exception as e:
                logger.error(f"Error in recon job execution: {str(e)}")
                raise
            finally:
                # Always release the lock when done
                release_lock(lock_name, db)
                logger.info("Released monitor_teams lock")
        else:
            # Lock contention on an hourly schedule almost always means the
            # previous run is still going, i.e. monitor_teams is overrunning its
            # window. Log at ERROR (not INFO) so it surfaces, and signal it
            # distinctly via the exit code - see main().
            logger.error(
                "Another process has the monitor_teams lock, cannot execute recon job. "
                "If this is the scheduled run, the previous run is overrunning its "
                "interval and monitoring is being skipped."
            )
            return False

    except Exception as e:
        logger.error(f"Error in recon job trigger: {str(e)}")
        raise
    finally:
        db.close()

    return True


def main():
    """Main function to run the script"""
    try:
        logger.info("Triggering recon job manually...")
        success = asyncio.run(trigger_recon_job())

        if success:
            logger.info("✅ Recon job completed successfully")
            sys.exit(0)
        else:
            logger.error(
                "⚠️  Recon job skipped: lock held by another process (likely an "
                "overrunning previous run)"
            )
            # EX_TEMPFAIL: distinguishable from success (0) and from a hard
            # failure (1), so a skipped run is alertable instead of silent.
            sys.exit(EXIT_LOCK_CONTENTION)

    except Exception as e:
        logger.error(f"❌ Script failed: {str(e)}")
        sys.exit(1)


if __name__ == "__main__":
    main()
