"""Daily record of each registry model's tracked state.

It adds a row only when the state changed since the model's newest row, so a
second run with no change adds nothing.
"""

from collections import defaultdict

from sqlalchemy.orm import Session

from app.registry.models import DBRegistryModel, DBRegistryModelHistory, DBRegistryModelSupport
from app.registry.runs import record_run

STEP = "history"


def _float(value):
    # A Decimal from the database and a float from JSON must compare equal.
    return None if value is None else float(value)


def apply_history(db: Session) -> dict:
    models = db.query(DBRegistryModel).all()

    support: dict[int, dict] = defaultdict(dict)
    for row in db.query(DBRegistryModelSupport):
        support[row.model_id][str(row.region_id)] = {
            "priced": row.priced,
            "supported": row.supported,
            "region_available": row.region_available,
        }

    # `id` breaks a tie, because the rows of one transaction share now().
    newest = {
        row.model_id: row.state
        for row in db.query(DBRegistryModelHistory)
        .distinct(DBRegistryModelHistory.model_id)
        .order_by(
            DBRegistryModelHistory.model_id,
            DBRegistryModelHistory.recorded_at.desc(),
            DBRegistryModelHistory.id.desc(),
        )
    }

    recorded = first = 0
    for model in models:
        state = {
            "status": model.status,
            "eol_date": model.eol_date.isoformat() if model.eol_date else None,
            "input_cost_per_token": _float(model.input_cost_per_token),
            "output_cost_per_token": _float(model.output_cost_per_token),
            "support": support.get(model.id, {}),
        }
        if newest.get(model.id) == state:
            continue
        db.add(DBRegistryModelHistory(model_id=model.id, state=state))
        recorded += 1
        if model.id not in newest:
            first += 1
    return {"models": len(models), "recorded": recorded, "first": first}


def run_history(db: Session) -> dict:
    return record_run(db, STEP, lambda: apply_history(db))
