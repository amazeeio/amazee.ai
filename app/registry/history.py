"""Daily record of each registry model's tracked state.

It adds a row only when the state changed since the model's newest row, so a
second run with no change adds nothing. Each recorded change also gives its
events, in the same transaction as the row.
"""

from collections import Counter, defaultdict

from sqlalchemy.orm import Session

from app.registry.models import DBRegistryEvent, DBRegistryModel, DBRegistryModelHistory, DBRegistryModelSupport
from app.registry.runs import record_run

STEP = "history"


def _float(value):
    # A Decimal from the database and a float from JSON must compare equal.
    return None if value is None else float(value)


def events_for(old, new, provider_known: bool) -> list[tuple[str, int | None, dict]]:
    """The events for one model between two states, as (type, region id, data).
    `provider_known` says that the model's provider had history before this run."""
    if old is None:
        # The first models of a provider are not news, or every model of a new provider looks new.
        return [("model.discovered", None, {})] if provider_known else []

    events = []
    if old["eol_date"] is None and new["eol_date"] is not None:
        events.append(("model.eol_announced", None, {"after": new["eol_date"]}))
    elif old["eol_date"] is not None and new["eol_date"] != old["eol_date"]:
        events.append(("model.eol_changed", None, {"before": old["eol_date"], "after": new["eol_date"]}))
    if new["status"] == "deprecated" and old["status"] != "deprecated":
        events.append(("model.deprecated", None, {"before": old["status"]}))
    # A proxy that appears or disappears is not a support change, or a new proxy gives one event for every model.
    for key in sorted(old["support"].keys() & new["support"].keys(), key=int):
        if old["support"][key] != new["support"][key]:
            events.append(
                ("model.support_changed", int(key), {"before": old["support"][key], "after": new["support"][key]})
            )
    return events


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
    # The providers that had history before this run.
    providers = {m.provider_id for m in models if m.id in newest}

    recorded = first = 0
    events = Counter()
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
        for type_, region_id, data in events_for(newest.get(model.id), state, model.provider_id in providers):
            db.add(DBRegistryEvent(type=type_, model_id=model.id, region_id=region_id, data=data))
            events[type_] += 1
        recorded += 1
        if model.id not in newest:
            first += 1
    return {"models": len(models), "recorded": recorded, "first": first, "events": dict(events)}


def run_history(db: Session) -> dict:
    return record_run(db, STEP, lambda: apply_history(db))
