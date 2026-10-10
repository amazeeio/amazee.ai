"""Daily choice of each registry model's EOL date among its sources.

It reads only the lifecycle rows that the other steps stored, so a second
run with the same rows changes nothing.
"""

from collections import defaultdict
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.registry import config
from app.registry.models import DBRegistryModelLifecycle, models_by_ident
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


def apply_eol(db: Session) -> dict:
    """Give each model the date of its source with the highest priority, or
    None when no source has a real date. On a tie, the lowest source name wins."""
    priority = priorities("eol_date")
    models = models_by_ident(db)
    best = strongest(
        db.query(DBRegistryModelLifecycle).filter(
            DBRegistryModelLifecycle.source.in_(priority),
            DBRegistryModelLifecycle.eol_date.isnot(None),
            DBRegistryModelLifecycle.eol_date < PLACEHOLDER_CUTOFF,
        ),
        priority,
    )

    changed = 0
    won: dict[str, int] = defaultdict(int)
    for ident, model in models.items():
        winner = best.get(ident)
        new = winner.eol_date if winner else None
        if winner:
            won[winner.source] += 1
        if model.eol_date != new:
            model.eol_date = new
            model.updated_at = datetime.now(UTC)
            changed += 1
    return {"models": len(models), "changed": changed, "won": dict(sorted(won.items()))}


def run_eol(db: Session) -> dict:
    return record_run(db, STEP, lambda: apply_eol(db))
