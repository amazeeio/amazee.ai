"""Run every source plugin, check its output, and write it to the registry.

Prices and specs only go to models the registry already has; which models
exist is decided by LiteLLM's list and the proxy import. Lifecycle and
regions are keyed by text, so they are stored for every model a source
lists, known or not. A plugin's output is untrusted data: it is checked in
full before anything is written.
"""

import logging
from datetime import UTC, date, datetime
from types import ModuleType

from sqlalchemy.orm import Session

from app.registry import config
from app.registry.discovery import finish_run
from app.registry.models import (
    DBRegistryCloudAvailability,
    DBRegistryModel,
    DBRegistryModelLifecycle,
    DBRegistryModelPrice,
    DBRegistryProvider,
    DBRegistryRun,
)
from app.registry.plugins.loader import load_plugins

logger = logging.getLogger(__name__)

SCHEMA = 1
_SCOPE_KINDS = {"geo": "geo", "region": "cloud_region"}
_LIFECYCLE_DATES = ("launched_at", "legacy_at", "extended_access_until", "eol_date")


def _scope(name) -> tuple[str, str]:
    if name == "base":
        return "base", ""
    kind, sep, value = str(name).partition(":")
    if not sep or not value or kind not in _SCOPE_KINDS:
        raise ValueError(f"unknown price scope {name!r}")
    return _SCOPE_KINDS[kind], value


def _count(value, what) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"{what} must be a whole number >= 0")
    return value


def validate(output, source: str) -> list[dict]:
    """The plugin's models in a checked, normalized form. Raises on any error."""
    if not isinstance(output, dict) or output.get("schema") != SCHEMA:
        raise ValueError(f"output must be an object with schema {SCHEMA}")
    if output.get("source") != source:
        raise ValueError(f"output source {output.get('source')!r} is not {source!r}")
    models = output.get("models")
    if not isinstance(models, list):
        raise ValueError("models must be a list")
    records, seen = [], set()
    for m in models:
        if not isinstance(m, dict) or not isinstance(m.get("provider"), str) or not m.get("model_id"):
            raise ValueError(f"model needs provider and model_id: {m!r:.200}")
        ident = (m["provider"], str(m["model_id"]))
        if ident in seen:
            continue
        seen.add(ident)
        prices = {}
        for name, fields in (m.get("prices") or {}).items():
            if not isinstance(fields, dict):
                raise ValueError(f"{ident}: prices for {name!r} must be an object")
            for field, value in fields.items():
                if "cost" not in field or isinstance(value, bool) or not isinstance(value, (int, float)) or value < 0:
                    raise ValueError(f"{ident}: bad price {field}={value!r}")
            if fields:
                prices[_scope(name)] = dict(sorted(fields.items()))
        lifecycle = m.get("lifecycle")
        if lifecycle is not None:
            if not isinstance(lifecycle, dict):
                raise ValueError(f"{ident}: lifecycle must be an object")
            lifecycle = {
                "status": lifecycle.get("status"),
                **{k: date.fromisoformat(lifecycle[k]) if lifecycle.get(k) else None for k in _LIFECYCLE_DATES},
            }
        regions = {}
        for region in m.get("regions") or []:
            if not isinstance(region, dict) or not isinstance(region.get("cloud_region"), str):
                raise ValueError(f"{ident}: region needs cloud_region")
            regions[region["cloud_region"]] = sorted(str(t) for t in region.get("call_types") or [])
        mode = m.get("mode")
        if mode is not None and not isinstance(mode, str):
            raise ValueError(f"{ident}: mode must be text")
        records.append(
            {
                "ident": ident,
                "mode": mode,
                "max_input_tokens": _count(m.get("max_input_tokens"), "max_input_tokens"),
                "max_output_tokens": _count(m.get("max_output_tokens"), "max_output_tokens"),
                "prices": prices,
                "lifecycle": lifecycle,
                "regions": regions,
            }
        )
    return records


def _apply_prices(db, model, own, source, role, prices, today, stats, fill_modes=None):
    mine = {(p.scope_kind, p.scope): p for p in own if p.source == source}
    others = [p for p in own if p.source != source]
    wrong_mode = fill_modes is not None and model.mode is not None and model.mode not in fill_modes
    if role == "fill" and (others or (not own and model.prices) or wrong_mode):
        # Another source prices the model, so a fill source steps aside.
        for p in mine.values():
            db.delete(p)
        return
    scopes = {(p.scope_kind, p.scope): p for p in own}
    for key, fields in prices.items():
        row = scopes.get(key)
        if row is None:
            db.add(
                DBRegistryModelPrice(
                    model_id=model.id, scope_kind=key[0], scope=key[1], prices=fields,
                    source=source, last_seen=today,
                )
            )
            stats["price_changes"] += 1
        else:
            if row.prices != fields or row.source != source:
                row.prices, row.source = fields, source
                stats["price_changes"] += 1
            row.last_seen = today
    for key, row in mine.items():
        if key not in prices:
            if key == ("base", "") and model.prices == row.prices:
                # The headline came from this row: unknown until the list's
                # next run prices the model again, not a stale plugin price.
                _set_headline(model, {})
            db.delete(row)
    # The headline is the base price only; a geo price never stands in for it.
    if ("base", "") in prices:
        _set_headline(model, prices[("base", "")])
    if prices:
        stats["priced"] += 1


def _set_headline(model, fields: dict) -> None:
    if model.prices != fields:
        model.prices = fields
        model.input_cost_per_token = fields.get("input_cost_per_token")
        model.output_cost_per_token = fields.get("output_cost_per_token")
        model.updated_at = datetime.now(UTC)


def apply_plugin(
    db: Session, source: str, role: str, records: list[dict], today: date, fill_modes: set | None = None
) -> dict:
    models = {
        (provider, m.model_id): m
        for m, provider in db.query(DBRegistryModel, DBRegistryProvider.name).join(DBRegistryProvider)
    }
    prices: dict[int, list[DBRegistryModelPrice]] = {}
    for p in db.query(DBRegistryModelPrice):
        prices.setdefault(p.model_id, []).append(p)

    # The models the source's last run listed; a broken source would drop most
    # of them. Older rows are left out: kept history must not raise the bar.
    by_id = {m.id: ident for ident, m in models.items()}
    seen = [(by_id[p.model_id], p.last_seen) for rows in prices.values() for p in rows if p.source == source]
    seen += [
        ((r.provider, r.model_id), r.last_seen)
        for r in db.query(DBRegistryModelLifecycle).filter_by(source=source)
    ]
    latest = max((day for _, day in seen), default=None)
    before = {ident for ident, day in seen if day == latest}
    listed = {r["ident"] for r in records}
    if before and len(before & listed) < len(before) * config.MIN_KEPT_RATIO:
        raise RuntimeError(f"{source} keeps {len(before & listed)} of {len(before)} known models; refusing to write")

    stats = {"models": len(records), "matched": 0, "unmatched": 0, "priced": 0, "price_changes": 0}
    availability = {
        (r.provider, r.model_id, r.cloud_region): r
        for r in db.query(DBRegistryCloudAvailability).filter_by(source=source)
    }
    for rec in records:
        model = models.get(rec["ident"])
        if model is None:
            stats["unmatched"] += 1
        else:
            stats["matched"] += 1
            _apply_prices(
                db, model, prices.get(model.id, []), source, role, rec["prices"], today, stats, fill_modes
            )
            # Specs only fill gaps: LiteLLM's list stays the source for them.
            for field in ("mode", "max_input_tokens", "max_output_tokens"):
                if getattr(model, field) is None and rec[field] is not None:
                    setattr(model, field, rec[field])
        provider, model_id = rec["ident"]
        if rec["lifecycle"] is not None:
            row = db.get(DBRegistryModelLifecycle, (provider, model_id, source))
            if row is None:
                row = DBRegistryModelLifecycle(provider=provider, model_id=model_id, source=source)
                db.add(row)
            for k, v in rec["lifecycle"].items():
                setattr(row, k, v)
            row.last_seen = today
        for region, call_types in rec["regions"].items():
            row = availability.pop((provider, model_id, region), None)
            if row is None:
                db.add(
                    DBRegistryCloudAvailability(
                        provider=provider, model_id=model_id, cloud_region=region,
                        source=source, call_types=call_types, last_seen=today,
                    )
                )
            else:
                row.call_types, row.last_seen = call_types, today
    # Availability means "offered now": every region this run did not list
    # goes, including all regions of a model the source dropped.
    for row in availability.values():
        db.delete(row)
    return stats


def run_plugin(db: Session, source: str, module: ModuleType | Exception, today: date) -> dict:
    run = DBRegistryRun(step=f"plugin:{source}", status="running")
    db.add(run)
    db.commit()
    try:
        if isinstance(module, Exception):
            raise module
        records = validate(module.parse(), source)
        stats = apply_plugin(
            db, source, module.PRICE_ROLE, records, today, getattr(module, "FILL_MODES", None)
        )
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats


def run_plugins(db: Session, today: date | None = None) -> dict:
    """Run every plugin; one that fails is recorded and the others still run."""
    results, failed = {}, []
    for source, module in load_plugins().items():
        try:
            results[source] = run_plugin(db, source, module, today or date.today())
        except Exception as e:
            logger.exception("Plugin %s failed", source)
            results[source] = {"error": str(e)}
            failed.append(source)
    if failed:
        raise RuntimeError(f"plugins failed: {', '.join(failed)}; results: {results}")
    return results
