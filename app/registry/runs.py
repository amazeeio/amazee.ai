"""One run record per registry step, whatever the step does."""

import logging
from datetime import UTC, datetime
from typing import Callable

from sqlalchemy.orm import Session

from app.registry.models import DBRegistryRun

logger = logging.getLogger(__name__)


def record_run(db: Session, step: str, work: Callable[[], dict], extra: dict | None = None) -> dict:
    """Run `work` in one transaction and record its stats or its error.

    The run row is committed first, so a step that crashes still leaves a
    `running` or `failed` record to find.
    """
    run = DBRegistryRun(step=step, status="running", stats=extra)
    db.add(run)
    db.commit()
    try:
        stats = {**(extra or {}), **work()}
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats


def finish_run(db: Session, run: DBRegistryRun) -> None:
    """Record the end of a run without hiding the error that ended it."""
    run.finished_at = datetime.now(UTC)
    try:
        db.commit()
    except Exception:
        db.rollback()
        logger.exception("Could not record the end of registry run %s", run.id)
