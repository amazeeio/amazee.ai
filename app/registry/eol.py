"""Daily choice of each registry model's EOL date among its sources.

It reads only the lifecycle rows that the other steps stored, so a second
run with the same rows changes nothing.
"""

from collections import defaultdict
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.registry import config
from app.registry.models import DBRegistryModel, DBRegistryModelLifecycle, models_by_ident
from app.registry.plugins.loader import load_plugins
from app.registry.runs import record_run

STEP = "eol"

# Sources use dates this far away to mean "no end date".
PLACEHOLDER_CUTOFF = date(2090, 1, 1)


def priorities(field: str) -> dict[str, int]:
    """The priority of `field` for LiteLLM's list (eol_date only) and for each
    enabled plugin that sets one."""
    # LiteLLM's lifecycle rows carry only a date, so it has no other priority.
    priority = {"litellm": config.LITELLM_EOL_PRIORITY} if field == "eol_date" else {}
    for source, module in load_plugins().items():
        if isinstance(module, Exception):
            continue
        plugin_priority = getattr(module, "PRIORITY", None) or {}  # the loader allows None
        if field in plugin_priority:
            priority[source] = plugin_priority[field]
    return priority


def strongest(rows, priority: dict[str, int]) -> dict:
    """(provider, model_id) -> the row of the source with the highest priority;
    on a tie, the lowest source name wins."""
    best = {}
    for row in sorted(rows, key=lambda r: (-priority[r.source], r.source)):
        best.setdefault((row.provider, row.model_id), row)
    return best


def _eol_rows(db: Session, priority: dict[str, int], ident: tuple[str, str] | None = None):
    """The lifecycle rows with a real date from a source in `priority`, for one model when `ident` is set."""
    query = db.query(DBRegistryModelLifecycle).filter(
        DBRegistryModelLifecycle.source.in_(priority),
        DBRegistryModelLifecycle.eol_date.isnot(None),
        DBRegistryModelLifecycle.eol_date < PLACEHOLDER_CUTOFF,
    )
    if ident is not None:
        query = query.filter(
            DBRegistryModelLifecycle.provider == ident[0], DBRegistryModelLifecycle.model_id == ident[1]
        )
    return query


def set_eol(model: DBRegistryModel, winner) -> bool:
    """Set the model's EOL date from its override, else from the winning row. True when it changed."""
    if model.override_eol is not None:
        new = model.override_eol
    else:
        new = winner.eol_date if winner else None
    if model.eol_date == new:
        return False
    model.eol_date = new
    model.updated_at = datetime.now(UTC)
    return True


def apply_eol(db: Session) -> dict:
    """Give each model its override, else the date of its source with the
    highest priority, or None when no source has a real date. On a tie, the
    lowest source name wins."""
    priority = priorities("eol_date")
    models = models_by_ident(db)
    best = strongest(_eol_rows(db, priority), priority)

    changed = 0
    won: dict[str, int] = defaultdict(int)
    for ident, model in models.items():
        winner = best.get(ident)
        if model.override_eol is not None:
            won["override_eol"] += 1
        elif winner:
            won[winner.source] += 1
        changed += set_eol(model, winner)
    return {"models": len(models), "changed": changed, "won": dict(sorted(won.items()))}


def refresh_eol(db: Session, model: DBRegistryModel, provider: str) -> bool:
    """Choose one model's EOL date now. It gives the same result as the daily step for this model."""
    priority = priorities("eol_date")
    winner = strongest(_eol_rows(db, priority, (provider, model.model_id)), priority).get(
        (provider, model.model_id)
    )
    return set_eol(model, winner)


def run_eol(db: Session) -> dict:
    return record_run(db, STEP, lambda: apply_eol(db))
