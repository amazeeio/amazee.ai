"""Daily read of each proxy's LiteLLM version, and one fetch per release list."""

import logging
import re
from datetime import UTC, datetime

import httpx
from sqlalchemy.orm import Session

from app.db.models import DBRegion
from app.registry import config
from app.registry.discovery import finish_run
from app.registry.litellm import ProxyClient, fetch_model_list
from app.registry.models import DBRegistryLitellmVersion, DBRegistryProxy, DBRegistryRun

logger = logging.getLogger(__name__)

STEP = "litellm-versions"

# The version comes from the proxy and goes into a URL, so only plain release
# numbers pass. A nightly or dev build has no release list anyway.
_RELEASE_VERSION = re.compile(r"^\d+\.\d+\.\d+$")


def active_regions(db: Session) -> list[DBRegion]:
    return (
        db.query(DBRegion)
        .filter(DBRegion.is_active.is_(True), DBRegion.litellm_api_url.isnot(None))
        .order_by(DBRegion.id)
        .all()
    )


def fetch_release_lists(db: Session, versions: set[str], now: datetime) -> dict:
    """Store the release list of each version that has none yet."""
    stored = {row.version: row for row in db.query(DBRegistryLitellmVersion)}
    fetched, failed = [], {}
    for version in sorted(versions):
        row = stored.get(version)
        if row is not None and row.payload is not None:
            continue
        if row is None:
            row = DBRegistryLitellmVersion(version=version)
            db.add(row)
        row.fetched_at = now
        if not _RELEASE_VERSION.match(version):
            row.error = "not a release version"
            failed[version] = row.error
            continue
        try:
            payload = fetch_model_list(config.RELEASE_LIST_URL.format(version=version))
        except (httpx.HTTPError, ValueError) as e:
            row.error = str(e)
            failed[version] = row.error
            continue
        row.payload, row.model_count, row.error = payload, len(payload), None
        fetched.append(version)
    db.flush()
    return {"fetched": fetched, "failed": failed}


def run_version_check(db: Session) -> dict:
    run = DBRegistryRun(step=STEP, status="running")
    db.add(run)
    db.commit()
    try:
        now = datetime.now(UTC)
        stats: dict = {"versions": {}, "unknown": []}
        for region in active_regions(db):
            try:
                with ProxyClient(region.litellm_api_url, region.litellm_api_key) as client:
                    version = client.version()
            except Exception as e:  # a bad URL or body in one region must not stop the rest
                logger.warning("Cannot read the LiteLLM version of %s: %s", region.name, e)
                version = None
            proxy = db.get(DBRegistryProxy, region.id)
            if version is None:
                # Keep the last known version; ren2-us hides /openapi.json.
                stats["unknown"].append(region.name)
                version = proxy.litellm_version if proxy else None
            else:
                if proxy is None:
                    proxy = DBRegistryProxy(region_id=region.id)
                    db.add(proxy)
                proxy.litellm_version = version
            if version:
                stats["versions"][region.name] = version
        stats.update(fetch_release_lists(db, set(stats["versions"].values()), now))
        run.status, run.stats = "ok", stats
        db.commit()
    except Exception as e:
        db.rollback()
        run.status, run.error = "failed", str(e)
        raise
    finally:
        finish_run(db, run)
    return stats
