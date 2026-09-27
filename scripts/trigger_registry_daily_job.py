#!/usr/bin/env python3
"""Daily registry job: proxy versions, the model list, then what each proxy can price and knows."""

import logging
import os
import sys

sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..")))

from sqlalchemy.orm import sessionmaker

from app.core.locking import release_lock, try_acquire_lock
from app.db.database import engine
from app.registry.bedrock_community import run_bedrock_catalog
from app.registry.discovery import run_discovery
from app.registry.models_dev import run_models_dev
from app.registry.plugins.runner import run_plugins
from app.registry.support import run_support_check
from app.registry.versions import run_version_check

logging.basicConfig(
    level=logging.INFO, format="%(asctime)s - %(name)s - %(levelname)s - %(message)s"
)
logger = logging.getLogger(__name__)

LOCK_NAME = "registry_daily"
# Fixed order: the support check compares the proxies with what the first two wrote.
STEPS = (
    ("version check", run_version_check),
    ("model list", run_discovery),
    # First-party plugins before models.dev, so it only fills what is left.
    ("source plugins", run_plugins),
    # After the model list, so it only fills what LiteLLM left unpriced.
    ("models.dev prices", run_models_dev),
    ("bedrock catalog", run_bedrock_catalog),
    ("support check", run_support_check),
)


def main():
    db = sessionmaker(autocommit=False, autoflush=False, bind=engine)()
    try:
        # 30 minutes outlasts a slow run and still expires long before the
        # next day, so one crashed run cannot block the next.
        if not try_acquire_lock(LOCK_NAME, db, lock_timeout=30):
            logger.warning("Another process holds the %s lock, skipping", LOCK_NAME)
            return
        failed = False
        try:
            for name, step in STEPS:
                # A failed step is recorded in registry_runs and must not stop the next one.
                try:
                    logger.info("Registry %s: %s", name, step(db))
                except Exception:
                    logger.exception("Registry %s failed", name)
                    db.rollback()
                    failed = True
        finally:
            db.rollback()  # a failed run can leave the session unusable for the lock release
            release_lock(LOCK_NAME, db)
        if failed:
            sys.exit(1)
    except Exception:
        logger.exception("Registry daily job failed")
        sys.exit(1)
    finally:
        db.close()


if __name__ == "__main__":
    main()
