"""Daily fallback prices from models.dev, for models no other source prices.

models.dev agrees with LiteLLM on most prices but not all, so it never
replaces a price from LiteLLM's list or from a proxy. It only fills a model
that has no price at all, and only for models whose output is text: its
image and audio prices do not fit per-token fields.
"""

import logging
from datetime import UTC, date, datetime

from sqlalchemy.orm import Session

from app.registry import config
from app.registry.discovery import finish_run
from app.registry.litellm import fetch_model_list, parse_key
from app.registry.models import DBRegistryModel, DBRegistryModelPrice, DBRegistryProvider, DBRegistryRun

logger = logging.getLogger(__name__)

STEP = "models-dev"
SOURCE = "models_dev"

# models.dev provider -> ours. Bedrock prices are for Bedrock's own endpoint,
# so they are not used for bedrock_mantle.
PROVIDERS = {
    "amazon-bedrock": "bedrock",
    "google-vertex": "vertex_ai",
    "google-vertex-anthropic": "vertex_ai",
    "deepinfra": "deepinfra",
    "openai": "openai",
    "azure": "azure",
}
# models.dev field (USD per million tokens) -> LiteLLM field (USD per token).
FIELDS = {
    "input": "input_cost_per_token",
    "output": "output_cost_per_token",
    "cache_read": "cache_read_input_token_cost",
    "cache_write": "cache_creation_input_token_cost",
}
TEXT_MODES = {"chat", "completion", "responses"}


def parse_models_dev(data: dict) -> dict[tuple[str, str], dict[tuple[str, str], dict]]:
    """Prices per (provider, model_id) and scope, in LiteLLM's per-token fields."""
    if not isinstance(data, dict):
        raise ValueError("models.dev payload is not an object")
    prices: dict[tuple[str, str], dict[tuple[str, str], dict]] = {}
    for source_provider, provider in PROVIDERS.items():
        for key, model in ((data.get(source_provider) or {}).get("models") or {}).items():
            cost = model.get("cost") if isinstance(model, dict) else None
            parsed = parse_key(key, provider)
            if not isinstance(cost, dict) or parsed is None:
                continue
            # Image and audio models put non-token prices in these fields.
            if (model.get("modalities") or {}).get("output") not in (None, ["text"]):
                continue
            fields = {
                # Rounded so 0.2 per million is stored as 2e-07, not 2.0000000000000002e-07.
                ours: float(f"{cost[theirs] / 1_000_000:.12g}")
                for theirs, ours in FIELDS.items()
                if isinstance(cost.get(theirs), (int, float)) and not isinstance(cost.get(theirs), bool)
            }
            if not fields:
                continue
            model_id, kind, scope = parsed
            # A geo id (`us.x`) is priced for that geo, so it is stored as the
            # geo's price, never as the base price.
            prices.setdefault((provider, model_id), {}).setdefault((kind, scope), fields)
    if not prices:
        raise ValueError("models.dev payload has no prices for our providers")
    return prices


def apply_models_dev(db: Session, listed: dict, today: date) -> dict:
    rows = {
        (provider, m.model_id): m
        for m, provider in db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
    }
    scopes: dict[int, list[DBRegistryModelPrice]] = {}
    for price in db.query(DBRegistryModelPrice):
        scopes.setdefault(price.model_id, []).append(price)

    stats = {"filled": 0, "scopes": 0, "handed_back": 0, "skipped_mode": 0, "unmatched": 0}
    for ident, model in rows.items():
        own = scopes.get(model.id, [])
        if any(p.source != SOURCE for p in own):
            # Another source prices the model now, so ours steps aside.
            for p in own:
                if p.source == SOURCE:
                    db.delete(p)
                    stats["handed_back"] += 1
            continue
        if not own and model.prices:
            # Priced before the price table existed, by another source.
            continue
        wanted = listed.get(ident)
        if not wanted:
            continue
        # Proxy-only models often have no mode; models.dev's modalities
        # already kept non-text models out.
        if model.mode is not None and model.mode not in TEXT_MODES:
            stats["skipped_mode"] += 1
            continue
        have = {(p.scope_kind, p.scope): p for p in own}
        for (kind, scope), fields in wanted.items():
            row = have.pop((kind, scope), None)
            if row is None:
                db.add(
                    DBRegistryModelPrice(
                        model_id=model.id, scope_kind=kind, scope=scope, prices=fields,
                        source=SOURCE, last_seen=today,
                    )
                )
            else:
                row.prices, row.last_seen = fields, today
            stats["scopes"] += 1
        for stale in have.values():
            db.delete(stale)
        headline = wanted.get(("base", "")) or next(iter(wanted.values()))
        if model.prices != headline:
            model.prices = headline
            model.input_cost_per_token = headline.get("input_cost_per_token")
            model.output_cost_per_token = headline.get("output_cost_per_token")
            model.updated_at = datetime.now(UTC)
        stats["filled"] += 1
    stats["unmatched"] = sum(1 for ident in listed if ident not in rows)
    return stats


def run_models_dev(db: Session, today: date | None = None) -> dict:
    run = DBRegistryRun(step=STEP, status="running")
    db.add(run)
    db.commit()
    try:
        stats = apply_models_dev(db, parse_models_dev(fetch_model_list(config.MODELS_DEV_URL)), today or date.today())
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats
