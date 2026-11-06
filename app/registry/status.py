"""Daily choice of each registry model's status among its sources.

It reads only the lifecycle rows that the other steps stored, so a second
run with the same rows changes nothing.
"""

from collections import defaultdict
from datetime import UTC, datetime

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.registry.eol import priorities, strongest
from app.registry.models import DBRegistryModelLifecycle, models_by_ident
from app.registry.runs import record_run

STEP = "status"

# A source's own status text, in upper case, and the registry status it gives.
# Any other text gives no opinion.
STATUS_MAP = {"LEGACY": "deprecated", "ACTIVE": "active"}


def apply_status(db: Session) -> dict:
    """Give each model the status of its source with the highest priority, or
    `active` when no source has an opinion. On a tie, the lowest source name wins."""
    priority = priorities("status")
    models = models_by_ident(db)
    best = strongest(
        db.query(DBRegistryModelLifecycle).filter(
            DBRegistryModelLifecycle.source.in_(priority),
            func.upper(DBRegistryModelLifecycle.status).in_(STATUS_MAP),
        ),
        priority,
    )

    changed = 0
    won: dict[str, int] = defaultdict(int)
    for ident, model in models.items():
        # `removed` beats `deprecated`; only the model list step clears it.
        if model.status == "removed":
            continue
        winner = best.get(ident)
        new = STATUS_MAP[winner.status.upper()] if winner else "active"
        if winner:
            won[winner.source] += 1
        if model.status != new:
            model.status = new
            model.updated_at = datetime.now(UTC)
            changed += 1
    return {"models": len(models), "changed": changed, "won": dict(sorted(won.items()))}


def run_status(db: Session) -> dict:
    return record_run(db, STEP, lambda: apply_status(db))
