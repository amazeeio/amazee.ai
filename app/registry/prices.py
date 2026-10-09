"""Model prices per scope, from LiteLLM's list, and the price a deployment should carry.

When the registry enables a model on a proxy it writes the price into the
deployment. LiteLLM uses a deployment's own price first, so the proxy then
charges this price whatever its own list says.
"""

import math
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.registry.litellm import our_entries, parse_key
from app.registry.models import DBRegistryModel, DBRegistryModelPrice, models_by_ident, prices_by_model
from app.registry.plugins.loader import override_sources

SOURCE = "litellm"


def price_fields(entry: dict) -> dict:
    # Some cost fields are objects, so only drop what no JSON column can store.
    return {
        k: v
        for k, v in sorted(entry.items())
        if "cost" in k and v is not None and not (isinstance(v, float) and not math.isfinite(v))
    }


def parse_prices(data: dict, providers: set[str]) -> dict[tuple[str, str], dict[tuple[str, str], dict]]:
    """Every priced scope of every model of our providers."""
    prices: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
    for key, provider, entry in our_entries(data, providers):
        parsed = parse_key(key, provider)
        fields = price_fields(entry)
        if parsed is None or not fields:
            continue
        model_id, kind, scope = parsed
        # Keys that differ only by a Bedrock route prefix name the same scope.
        prices.setdefault((provider, model_id), {}).setdefault((kind, scope), fields)
    return prices


def apply_prices(
    db: Session,
    listed_models: set[tuple[str, str]],
    listed_prices: dict[tuple[str, str], dict[tuple[str, str], dict]],
    today: date,
) -> dict:
    """Make the stored scopes of each listed model match the list.

    A scope the list dropped is deleted, so it can never be picked. Models
    that left the list, and prices taken from a proxy at import, are kept.
    """
    models = models_by_ident(db)
    stored = {
        model_id: {(p.scope_kind, p.scope): p for p in rows} for model_id, rows in prices_by_model(db).items()
    }

    # A first-party plugin's price beats the list's; leave those rows alone.
    locked = override_sources()
    stats = {"price_scopes": 0, "price_changes": 0, "price_scopes_dropped": 0}
    for ident in listed_models:
        model = models.get(ident)
        if model is None:
            continue
        wanted = listed_prices.get(ident, {})
        have = stored.get(model.id, {})
        stats["price_scopes"] += len(wanted)
        # The list's price replaces one the import took from a proxy.
        stats["price_changes"] += upsert_scopes(db, model.id, have, wanted, SOURCE, today, keep=locked)
        for key, row in have.items():
            # Only the list's own scopes are dropped; a proxy price stays.
            if key not in wanted and row.source == SOURCE:
                db.delete(row)
                stats["price_scopes_dropped"] += 1
    return stats


def upsert_scopes(
    db: Session, model_id: int, have: dict, wanted: dict, source: str, today: date, keep=frozenset()
) -> int:
    """Write `wanted` scopes as `source`'s; rows of a `keep` source stay as
    they are. Returns how many scopes were added or changed."""
    changes = 0
    for (kind, scope), fields in wanted.items():
        row = have.get((kind, scope))
        if row is None:
            db.add(
                DBRegistryModelPrice(
                    model_id=model_id, scope_kind=kind, scope=scope, prices=fields,
                    source=source, last_seen=today,
                )
            )
            changes += 1
        elif row.source not in keep:
            if row.prices != fields or row.source != source:
                row.prices, row.source = fields, source
                changes += 1
            row.last_seen = today
    return changes


def set_headline(model: DBRegistryModel, fields: dict) -> None:
    """The model row's own price fields, from its base price."""
    if model.prices != fields:
        model.prices = fields
        model.input_cost_per_token = fields.get("input_cost_per_token")
        model.output_cost_per_token = fields.get("output_cost_per_token")
        model.updated_at = datetime.now(UTC)


def pick_price(
    db: Session, model_id: int, provider: str, deployed_model: str | None, cloud_region: str | None
) -> DBRegistryModelPrice | None:
    """The price a deployment of the model should carry.

    A geo id (`us.x`, `azure/eu/x`) is billed at its geo price. Otherwise the
    price of the proxy's cloud region wins when there is one. Base comes last.
    This follows LiteLLM, which looks up the region price with the model
    string as deployed, so a geo id never gets a region price.
    """
    scopes = {(p.scope_kind, p.scope): p for p in db.query(DBRegistryModelPrice).filter_by(model_id=model_id)}
    return next((scopes[key] for key in price_order(provider, deployed_model, cloud_region) if key in scopes), None)


def price_order(provider: str, deployed_model: str | None, cloud_region: str | None) -> list[tuple[str, str]]:
    """The scopes LiteLLM tries for a deployment, first match wins."""
    parsed = parse_key(deployed_model, provider) if deployed_model else None
    if parsed and parsed[1] == "geo":
        order = [("geo", parsed[2])]
    else:
        order = [("cloud_region", cloud_region)] if cloud_region else []
    order.append(("base", ""))
    return order
