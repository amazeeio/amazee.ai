"""Model prices per scope, from LiteLLM's list, and the price a deployment should carry.

When the registry enables a model on a proxy it writes the price into the
deployment. LiteLLM uses a deployment's own price first, so the proxy then
charges this price whatever its own list says.
"""

from datetime import date

from sqlalchemy.orm import Session

from app.registry.litellm import normalize_provider, parse_key
from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider

SOURCE = "litellm"


def price_fields(entry: dict) -> dict:
    return {k: v for k, v in sorted(entry.items()) if "cost" in k and v is not None}


def parse_prices(data: dict, providers: set[str]) -> dict[tuple[str, str], dict[tuple[str, str], dict]]:
    """Every priced scope of every model of our providers."""
    prices: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
    for key, entry in data.items():
        if not isinstance(entry, dict) or key == "sample_spec":
            continue
        provider = normalize_provider(entry.get("litellm_provider"))
        if provider not in providers:
            continue
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
    that left the list keep their last prices.
    """
    model_ids = {
        (provider, row.model_id): row.id
        for row, provider in db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
    }
    stored: dict[int, dict[tuple[str, str], DBRegistryModelPrice]] = {}
    for row in db.query(DBRegistryModelPrice).filter_by(source=SOURCE):
        stored.setdefault(row.model_id, {})[(row.scope_kind, row.scope)] = row

    stats = {"price_scopes": 0, "price_changes": 0, "price_scopes_dropped": 0}
    for ident in listed_models:
        model_id = model_ids.get(ident)
        if model_id is None:
            continue
        wanted = listed_prices.get(ident, {})
        have = stored.get(model_id, {})
        for (kind, scope), fields in wanted.items():
            stats["price_scopes"] += 1
            row = have.get((kind, scope))
            if row is None:
                db.add(
                    DBRegistryModelPrice(
                        model_id=model_id,
                        scope_kind=kind,
                        scope=scope,
                        prices=fields,
                        source=SOURCE,
                        last_seen=today,
                    )
                )
                stats["price_changes"] += 1
                continue
            if row.prices != fields:
                row.prices = fields
                stats["price_changes"] += 1
            row.last_seen = today
        for key, row in have.items():
            if key not in wanted:
                db.delete(row)
                stats["price_scopes_dropped"] += 1
    return stats


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
    parsed = parse_key(deployed_model, provider) if deployed_model else None
    if parsed and parsed[1] == "geo":
        order = [("geo", parsed[2])]
    else:
        order = [("cloud_region", cloud_region)] if cloud_region else []
    order.append(("base", ""))
    return next((scopes[key] for key in order if key in scopes), None)
