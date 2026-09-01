"""Model end-of-life dates: one upstream source, stored on the model row.

The only source is the AWS Bedrock catalog at ``BEDROCK_MODELS_URL``. A daily
cron resolves a date for every model we serve and writes it to
``models.upstream_eol``; ``/public/models`` and the catalog prune gate read that
column. Before this, an EOL date was resolved live per request and also existed
as a hand-written ``(EOL: ...)`` note in LiteLLM metadata, so the same model
could carry two different dates.

Non-Bedrock providers (Vertex, Azure) publish no lifecycle data, so their models
never get a date. Aliases have none either: an alias is repointed at a newer
model rather than retired.

The webhook is a full snapshot, not a per-change event: every model with a date
is sent every day. That is self-healing — a lost delivery is corrected by the
next run — and the consumer de-duplicates on ``event_id``, which is derived from
the model and the date rather than randomly generated.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import UTC, date, datetime, timedelta
from typing import Any

import httpx
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DBModel, DBRegion
from app.services.litellm import LiteLLMService

logger = logging.getLogger(__name__)

# Bumped when the event shape changes in a way consumers must notice.
MODEL_EOL_API_VERSION = "2026-08-27"

EVENT_TYPE = "model.eol_announced"

_REGION_TIMEOUT = 30.0  # seconds per region; a cron can wait longer than a request
_REGION_CONCURRENCY = 4
_WEBHOOK_TIMEOUT = 30.0  # seconds; one POST a day, no reason to make it a knob

_BEDROCK_CATALOG_TTL = timedelta(hours=1)
_bedrock_catalog_lock = asyncio.Lock()
_bedrock_catalog_cache: dict[str, Any] = {
    "url": None,
    "expires_at": datetime.min.replace(tzinfo=UTC),
    "data": None,
}

# ``bedrock/us.anthropic.claude-...`` -> the catalog's ``anthropic.claude-...``.
_BEDROCK_REGION_PREFIXES = ("us.", "eu.", "au.", "apac.", "global.", "jp.")


# ---------------------------------------------------------------------------
# Upstream catalog
# ---------------------------------------------------------------------------


async def fetch_bedrock_catalog(url: str) -> list[dict[str, Any]]:
    """Fetch the upstream Bedrock model catalog, with a small in-memory cache.

    The catalog is on the order of a few hundred KB and changes infrequently,
    so a 1h TTL is plenty.  Cache key includes the URL so overrides bypass
    stale data automatically.

    Raises ``ValueError`` on a response that is not a JSON array, and lets
    httpx errors through — callers decide whether that is fatal.
    """
    now = datetime.now(UTC)
    if (
        _bedrock_catalog_cache["url"] == url
        and _bedrock_catalog_cache["expires_at"] > now
        and _bedrock_catalog_cache["data"] is not None
    ):
        return _bedrock_catalog_cache["data"]

    async with _bedrock_catalog_lock:
        now = datetime.now(UTC)
        if (
            _bedrock_catalog_cache["url"] == url
            and _bedrock_catalog_cache["expires_at"] > now
            and _bedrock_catalog_cache["data"] is not None
        ):
            return _bedrock_catalog_cache["data"]

        timeout = settings.BEDROCK_MISSING_MODELS_TIMEOUT_SECONDS
        async with httpx.AsyncClient(timeout=timeout) as client:
            response = await client.get(url)
            response.raise_for_status()
            try:
                data = response.json()
            except ValueError as exc:
                raise ValueError(
                    f"Upstream Bedrock catalog at {url} returned non-JSON response: {exc}"
                ) from exc

        if not isinstance(data, list):
            raise ValueError(
                f"Upstream Bedrock catalog at {url} did not return a JSON array"
            )

        _bedrock_catalog_cache["url"] = url
        _bedrock_catalog_cache["data"] = data
        _bedrock_catalog_cache["expires_at"] = now + _BEDROCK_CATALOG_TTL
        return data


def bedrock_catalog_id(item: dict[str, Any]) -> str | None:
    """Return the upstream Bedrock ``modelId`` for a LiteLLM entry, or None.

    Returns None for non-Bedrock providers (``vertex_ai/...``, ``azure/...``),
    which the Bedrock catalog cannot describe.
    """
    params = item.get("litellm_params")
    candidate = params.get("model") if isinstance(params, dict) else None
    if not isinstance(candidate, str) or not candidate:
        return None
    if candidate.startswith("bedrock/"):
        candidate = candidate.split("/", 1)[1]
    elif "/" in candidate:
        return None
    for prefix in _BEDROCK_REGION_PREFIXES:
        if candidate.startswith(prefix):
            candidate = candidate[len(prefix) :]
            break
    return candidate or None


def build_eol_index(catalog: list[dict[str, Any]]) -> dict[str, str | None]:
    """Map Bedrock ``modelId`` -> ISO EOL date for every model that has one.

    Two upstream fields carry the date and they agree wherever both are set, but
    neither covers the other's models: prefer ``modelLifecycle.endOfLifeTime``
    (already ISO), fall back to the human-formatted ``modelCard.modelEolDate``.

    A ``None`` value means the entry carried a date we could not parse. That is
    not the same as carrying no date: a garbled date tells us nothing, so the
    caller must leave any stored date alone rather than read it as a withdrawal.
    Models with no date field at all are simply absent from the index.
    """
    index: dict[str, str | None] = {}
    _unparseable_lifecycle: set[str] = set()
    for model in catalog:
        if not isinstance(model, dict):
            continue
        model_id = model.get("modelId")
        if not isinstance(model_id, str) or not model_id:
            continue

        lifecycle = model.get("modelLifecycle")
        raw = (
            (lifecycle or {}).get("endOfLifeTime")
            if isinstance(lifecycle, dict)
            else None
        )
        if isinstance(raw, str) and raw.strip():
            try:
                index[model_id] = date.fromisoformat(raw.strip()[:10]).isoformat()
                continue
            except ValueError:
                _unparseable_lifecycle.add(model_id)
                logger.debug(
                    "Unparseable modelLifecycle.endOfLifeTime %r for Bedrock model "
                    "%s; trying modelCard.modelEolDate",
                    raw,
                    model_id,
                )

        card = model.get("modelCard")
        raw = (card or {}).get("modelEolDate") if isinstance(card, dict) else None
        if isinstance(raw, str) and raw.strip():
            try:
                index[model_id] = (
                    datetime.strptime(raw.strip(), "%B %d, %Y").date().isoformat()
                )
            except ValueError:
                logger.warning(
                    "Unparseable EOL date for Bedrock model %s (%r); leaving any "
                    "stored date untouched",
                    model_id,
                    raw,
                )
                index[model_id] = None
        elif model_id in _unparseable_lifecycle:
            # The lifecycle field held a date we could not read and there is no
            # card date to fall back on.
            index[model_id] = None
    return index


# ---------------------------------------------------------------------------
# Stored dates
# ---------------------------------------------------------------------------


def eol_dates_by_model(db: Session) -> dict[str, str]:
    """Stored EOL dates as ``{model_id: "YYYY-MM-DD"}`` for live models."""
    rows = (
        db.query(DBModel.model_id, DBModel.upstream_eol)
        .filter(
            DBModel.deleted_at.is_(None),
            DBModel.upstream_eol.isnot(None),
        )
        .all()
    )
    return {model_id: eol.date().isoformat() for model_id, eol in rows}


async def _region_model_names_to_catalog_ids(
    region: DBRegion, semaphore: asyncio.Semaphore
) -> dict[str, str] | None:
    """``{model_name: bedrock_model_id}`` for one region, or None if unreachable.

    None and an empty dict mean different things: a region we could not read
    must not be treated as "serves nothing", or a transient outage would look
    like every model there losing its EOL date.
    """
    service = LiteLLMService(
        api_url=region.litellm_api_url, api_key=region.litellm_api_key
    )
    async with semaphore:
        try:
            model_info = await asyncio.wait_for(
                service.get_model_info(), timeout=_REGION_TIMEOUT
            )
        except Exception as exc:  # noqa: BLE001 - any failure is "unreadable"
            logger.warning(
                "Region %s unreadable for the EOL scan: %s", region.name, str(exc)
            )
            return None

    mapping: dict[str, str] = {}
    for item in model_info.get("data", []) or []:
        if not isinstance(item, dict):
            continue
        name = item.get("model_name")
        catalog_id = bedrock_catalog_id(item)
        if isinstance(name, str) and name and catalog_id:
            mapping[name] = catalog_id
    return mapping


def _to_datetime(iso_date: str) -> datetime:
    return datetime.combine(
        date.fromisoformat(iso_date), datetime.min.time(), tzinfo=UTC
    )


def _apply_dates(
    db: Session,
    resolved: dict[str, str],
    observed: set[str],
) -> tuple[int, int]:
    """Write ``upstream_eol`` for observed models. Returns (set, cleared).

    ``observed`` is every model name we read from at least one region *and*
    found described in the upstream catalog. Only those may have a date cleared
    — a model we could not see this run, or that upstream no longer describes,
    keeps whatever it had.
    """
    if not observed:
        return 0, 0

    now = datetime.now(UTC)
    # Aliases are excluded on purpose. A fake-alias LiteLLM entry carries its
    # target's backend model in litellm_params.model, so it resolves to the
    # target's catalog id -- without this filter an alias would inherit the
    # target's date, and an alias is repointed rather than retired.
    rows = (
        db.query(DBModel)
        .filter(
            DBModel.deleted_at.is_(None),
            DBModel.is_alias.is_(False),
            DBModel.model_id.in_(observed),
        )
        .all()
    )
    set_count = cleared_count = 0
    for row in rows:
        iso = resolved.get(row.model_id)
        wanted = _to_datetime(iso) if iso else None
        if row.upstream_eol == wanted:
            continue
        row.upstream_eol = wanted
        # A changed or withdrawn date is a new announcement, so the model is
        # eligible to be reported as newly notified again.
        row.upstream_eol_first_seen_at = now if wanted else None
        row.eol_notified_at = None
        if wanted:
            set_count += 1
        else:
            cleared_count += 1

    missing = observed - {row.model_id for row in rows}
    if missing:
        logger.info(
            "EOL scan saw %d served model(s) with no models row: %s",
            len(missing),
            ", ".join(sorted(missing)[:10]),
        )
    return set_count, cleared_count


def _build_events(
    db: Session, regions_by_model: dict[str, list[tuple[int, str]]]
) -> tuple[list[dict], list[DBModel]]:
    """One event per model that has a date. Also returns the un-notified rows."""
    # A globally deactivated model is already gone from the callable surface, so
    # announcing its EOL every day would be noise.
    rows = (
        db.query(DBModel)
        .filter(
            DBModel.deleted_at.is_(None),
            DBModel.is_alias.is_(False),
            DBModel.is_active_globally.is_(True),
            DBModel.upstream_eol.isnot(None),
        )
        .order_by(DBModel.model_id)
        .all()
    )
    created_at = datetime.now(UTC).isoformat()
    events = []
    for row in rows:
        eol_date = row.upstream_eol.date().isoformat()
        events.append(
            {
                "event_id": f"model_eol:{row.model_id}:{eol_date}",
                "type": EVENT_TYPE,
                "created_at": created_at,
                "data": {
                    "model_id": row.model_id,
                    "display_name": row.display_name,
                    "eol_date": eol_date,
                    "first_seen_at": (
                        row.upstream_eol_first_seen_at.isoformat()
                        if row.upstream_eol_first_seen_at
                        else None
                    ),
                    "regions": [
                        {"id": rid, "name": name}
                        for rid, name in sorted(
                            regions_by_model.get(row.model_id, []),
                            key=lambda region: region[1],
                        )
                    ],
                },
            }
        )
    return events, [row for row in rows if row.eol_notified_at is None]


async def _deliver(events: list[dict]) -> bool:
    """POST the whole snapshot as one request. True when accepted.

    Deliberately not reusing ``budget_alert_webhook.deliver_events``: that
    module's contract is per-event retry bookkeeping, which a daily full
    snapshot does not need. It shares only the envelope shape.
    """
    url = settings.MODEL_EOL_WEBHOOK_URL
    if not url:
        logger.error(
            "MODEL_EOL_WEBHOOK_URL is not set; %d model EOL event(s) not delivered",
            len(events),
        )
        return False

    headers = {"Content-Type": "application/json"}
    if settings.MODEL_EOL_WEBHOOK_TOKEN:
        headers["Authorization"] = f"Bearer {settings.MODEL_EOL_WEBHOOK_TOKEN}"

    payload = {"api_version": MODEL_EOL_API_VERSION, "events": events}
    try:
        async with httpx.AsyncClient(timeout=_WEBHOOK_TIMEOUT) as client:
            response = await client.post(url, json=payload, headers=headers)
    except httpx.RequestError as exc:
        logger.error(
            "Model EOL webhook unreachable (%d event(s) will retry tomorrow): %s",
            len(events),
            exc,
        )
        return False

    if 200 <= response.status_code < 300:
        logger.info(
            "Delivered %d model EOL event(s) (status %s)",
            len(events),
            response.status_code,
        )
        return True

    logger.error(
        "Model EOL webhook returned %s (%d event(s) will retry tomorrow): %s",
        response.status_code,
        len(events),
        response.text[:500],
    )
    return False


async def scan_models_for_eol(db: Session) -> dict[str, int]:
    """Resolve EOL dates from upstream, store them, and send today's snapshot."""
    if not settings.BEDROCK_MODELS_URL:
        logger.warning("BEDROCK_MODELS_URL is empty; skipping the model EOL scan")
        return {}

    try:
        catalog = await fetch_bedrock_catalog(settings.BEDROCK_MODELS_URL)
    except (httpx.HTTPError, ValueError) as exc:
        # Abort without touching the DB: an unreachable catalog must not look
        # like every model losing its EOL date.
        logger.error("Bedrock catalog unavailable; EOL scan aborted: %s", str(exc))
        return {}

    eol_index = build_eol_index(catalog)
    if not any(eol_index.values()):
        # A catalog with zero dates in it means the feed changed shape, not that
        # AWS withdrew every retirement. Continuing would clear every stored
        # date, so stop before touching the DB.
        logger.error(
            "Bedrock catalog carried no EOL dates at all; EOL scan aborted "
            "(the upstream feed may have changed shape)"
        )
        return {}

    # Every id the catalog describes, dated or not. A backend id missing from
    # this set means upstream says nothing about the model -- a renamed id or a
    # dropped entry -- so its stored date must be left alone. Only an id the
    # catalog does describe, with no date on it, is a real withdrawal.
    catalog_ids = {
        model["modelId"]
        for model in catalog
        if isinstance(model, dict) and isinstance(model.get("modelId"), str)
    }

    regions = db.query(DBRegion).filter(DBRegion.is_active.is_(True)).all()
    semaphore = asyncio.Semaphore(_REGION_CONCURRENCY)
    mappings = await asyncio.gather(
        *(_region_model_names_to_catalog_ids(region, semaphore) for region in regions)
    )

    observed: set[str] = set()
    uncertain: set[str] = set()
    resolved: dict[str, str] = {}
    regions_by_model: dict[str, list[tuple[int, str]]] = {}
    for region, mapping in zip(regions, mappings):
        if mapping is None:
            continue
        for model_name, catalog_id in mapping.items():
            # The payload reports every region seen serving the model, whether or
            # not that region's backend id resolved to a date.
            regions_by_model.setdefault(model_name, []).append(
                (region.id, region.name)
            )
            if catalog_id not in catalog_ids:
                continue
            if catalog_id in eol_index and eol_index[catalog_id] is None:
                # Upstream carried a date we could not parse. Saying nothing is
                # right; treating it as a withdrawal would drop a real date.
                # Marked per callable name, not per mapping: another region can
                # serve the same name from a dateless backend id, and that must
                # not turn the unreadable date into a withdrawal.
                uncertain.add(model_name)
                continue
            observed.add(model_name)
            if eol := eol_index.get(catalog_id):
                # Regions can point the same callable name at different backend
                # model ids with different dates. Take the earliest: it is the
                # honest warning, and it keeps the announced date and the prune
                # gate on the same day whatever order the regions are read in.
                current = resolved.get(model_name)
                resolved[model_name] = min(current, eol) if current else eol

    # A name upstream was unclear about is left untouched, even if it resolved
    # a date elsewhere: we cannot tell which mapping is the real one.
    observed -= uncertain
    resolved = {name: eol for name, eol in resolved.items() if name not in uncertain}

    set_count, cleared_count = _apply_dates(db, resolved, observed)
    db.commit()

    events, unnotified = _build_events(db, regions_by_model)
    delivered = False
    if events:
        delivered = await _deliver(events)
        if delivered and unnotified:
            now = datetime.now(UTC)
            for row in unnotified:
                row.eol_notified_at = now
            db.commit()

    totals = {
        "regions_read": sum(1 for m in mappings if m is not None),
        "regions_unreadable": sum(1 for m in mappings if m is None),
        "models_observed": len(observed),
        "dates_set": set_count,
        "dates_cleared": cleared_count,
        "events_sent": len(events) if delivered else 0,
        "newly_notified": len(unnotified) if delivered else 0,
    }
    logger.info("Model EOL scan: %s", totals)
    return totals
