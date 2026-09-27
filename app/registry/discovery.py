"""Daily update of `registry_models` from LiteLLM's model list on `main`."""

import logging
from datetime import UTC, date, datetime
from decimal import Decimal

from sqlalchemy.orm import Session

from app.registry import config
from app.registry.litellm import fetch_model_list, normalize_provider, split_model
from app.registry.models import DBRegistryModel, DBRegistryProvider, DBRegistryRun
from app.registry.prices import apply_prices, parse_prices, price_fields

logger = logging.getLogger(__name__)

STEP = "litellm-list"

# Columns the list owns. Anything else on the row belongs to us.
LIST_FIELDS = (
    "mode",
    "max_input_tokens",
    "max_output_tokens",
    "input_cost_per_token",
    "output_cost_per_token",
    "supports",
    "eol_date",
)


def _as_int(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _as_decimal(value):
    try:
        return Decimal(str(value)) if value is not None else None
    except ArithmeticError:
        return None


def _as_date(value):
    try:
        return date.fromisoformat(str(value)[:10]) if value else None
    except ValueError:
        return None


def entry_fields(entry: dict) -> dict:
    return {
        "mode": entry.get("mode"),
        "max_input_tokens": _as_int(entry.get("max_input_tokens")),
        "max_output_tokens": _as_int(entry.get("max_output_tokens") or entry.get("max_tokens")),
        "input_cost_per_token": _as_decimal(entry.get("input_cost_per_token")),
        "output_cost_per_token": _as_decimal(entry.get("output_cost_per_token")),
        "prices": price_fields(entry),
        "supports": sorted(
            k.removeprefix("supports_") for k, v in entry.items() if k.startswith("supports_") and v is True
        ),
        "eol_date": _as_date(entry.get("deprecation_date")),
    }


def parse_model_list(data: dict, providers: set[str]) -> tuple[dict[tuple[str, str], dict], int]:
    """One entry per (provider, model_id), for our providers only.

    A Bedrock geo entry (`us.x`) stands in for its base model only when the
    list has no base entry (`x`). Returns the models and the count of
    entries skipped as price variants.
    """
    models: dict[tuple[str, str], dict] = {}
    from_geo: set[tuple[str, str]] = set()
    variants = 0
    for key, entry in data.items():
        if not isinstance(entry, dict) or key == "sample_spec":
            continue
        provider = normalize_provider(entry.get("litellm_provider"))
        if provider not in providers:
            continue
        split = split_model(key, provider)
        if split is None:
            variants += 1
            continue
        model_id, is_geo = split
        ident = (provider, model_id)
        if ident in models and (is_geo or ident not in from_geo):
            continue
        models[ident] = entry_fields(entry)
        if is_geo:
            from_geo.add(ident)
        else:
            from_geo.discard(ident)
    return models, variants


def apply_model_list(db: Session, listed: dict[tuple[str, str], dict], today: date) -> dict:
    """Insert new models, refresh listed ones, mark the ones that left as removed."""
    providers = {p.name: p.id for p in db.query(DBRegistryProvider).all()}
    by_ident = {
        (row_provider, row.model_id): row
        for row, row_provider in db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
    }
    # Only active rows can be newly removed, so only they count here.
    at_risk = {
        ident for ident, row in by_ident.items() if row.source == "litellm" and row.status == "active"
    }
    kept = len(at_risk & listed.keys())
    if at_risk and kept < len(at_risk) * config.MIN_KEPT_RATIO:
        raise RuntimeError(
            f"list keeps {kept} of {len(at_risk)} known models; refusing to remove the rest"
        )

    stats = {"listed": len(listed), "inserted": 0, "updated": 0, "removed": 0}
    for (provider, model_id), fields in listed.items():
        row = by_ident.get((provider, model_id))
        if row is None:
            db.add(
                DBRegistryModel(
                    provider_id=providers[provider],
                    model_id=model_id,
                    status="active",
                    source="litellm",
                    first_seen=today,
                    last_seen=today,
                    **fields,
                )
            )
            stats["inserted"] += 1
            continue
        changed = {k: v for k, v in fields.items() if getattr(row, k) != v}
        if row.status != "active" or row.source != "litellm":
            changed.update(status="active", source="litellm")
        for k, v in changed.items():
            setattr(row, k, v)
        if changed:
            row.updated_at = datetime.now(UTC)
        row.last_seen = today
        stats["updated"] += bool(changed)

    for ident, row in by_ident.items():
        if ident not in listed and row.source == "litellm" and row.status != "removed":
            row.status = "removed"
            row.updated_at = datetime.now(UTC)
            stats["removed"] += 1
    return stats


def run_discovery(db: Session, today: date | None = None) -> dict:
    run = DBRegistryRun(step=STEP, status="running")
    db.add(run)
    db.commit()
    try:
        providers = {p.name for p in db.query(DBRegistryProvider).all()}
        if not providers:
            # Providers come from the proxy import. Without them every model
            # would be filtered out, so there is nothing to do yet.
            logger.warning("No registry providers yet; run the proxy import first")
            stats = {"listed": 0, "inserted": 0, "updated": 0, "removed": 0}
        else:
            data = fetch_model_list(config.LITELLM_LIST_URL)
            listed, variants = parse_model_list(data, providers)
            stats = apply_model_list(db, listed, today or date.today())
            stats["price_variants_skipped"] = variants
            db.flush()  # new models need ids before their prices are stored
            stats.update(apply_prices(db, set(listed), parse_prices(data, providers), today or date.today()))
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
