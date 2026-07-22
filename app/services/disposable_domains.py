"""Disposable / dynamic-DNS email-domain blocking (trial-account abuse protection, moad #620).

Defense-in-depth for the amazee.ai backend: it must not blindly trust callers
(moad, Drupal, direct API) to have filtered signup emails.

Design (mirrors the Keycloak plugin's practice, but persisted):
  - The blocklist lives in the ``disposable_domains`` DB table (shared across pods).
  - A cron job calls ``refresh_disposable_domains`` once per day to (re)populate it
    from a committed baseline (dynv6.net + dynamic-DNS providers) merged with the
    upstream ``disposable-email-domains`` list.
  - Signup paths cross-check the email's domain against the table, with
    suffix/subdomain matching so blocking ``dynv6.net`` also blocks ``a.b.dynv6.net``.
"""

import logging
from pathlib import Path
from typing import Iterable

import httpx
from fastapi import HTTPException, status
from sqlalchemy.orm import Session

from app.core.config import settings
from app.db.models import DBDisposableDomain

logger = logging.getLogger(__name__)

_BASELINE_FILE = (
    Path(__file__).resolve().parent.parent / "data" / "disposable_domains_extra.txt"
)

SOURCE_BASELINE = "baseline"
SOURCE_UPSTREAM = "upstream"


def _parse_domains(lines: Iterable[str]) -> set[str]:
    domains: set[str] = set()
    for raw in lines:
        line = raw.strip().lower()
        if not line or line.startswith("#"):
            continue
        if "@" in line:  # tolerate list entries that are full emails / have "@"
            line = line.rsplit("@", 1)[-1]
        line = line.strip(".")
        if line:
            domains.add(line)
    return domains


def extract_domain(email_or_domain: str) -> str:
    """Return the lowercased domain part of an email (or a bare domain)."""
    value = (email_or_domain or "").strip().lower()
    if "@" in value:
        value = value.rsplit("@", 1)[-1]
    return value.strip().strip(".")


def candidate_suffixes(domain: str) -> list[str]:
    """Parent suffixes to test, e.g. a.b.dynv6.net -> [a.b.dynv6.net, b.dynv6.net, dynv6.net].

    Stops at the registrable-pair level (two labels); never returns a bare TLD.
    This is what makes blocking an apex domain also block all of its subdomains.
    """
    labels = [p for p in domain.split(".") if p]
    if len(labels) < 2:
        return [domain] if domain else []
    return [".".join(labels[i:]) for i in range(0, len(labels) - 1)]


def baseline_domains() -> set[str]:
    """The committed custom baseline (file) plus DISPOSABLE_DOMAINS_EXTRA."""
    domains: set[str] = set()
    try:
        domains |= _parse_domains(
            _BASELINE_FILE.read_text(encoding="utf-8").splitlines()
        )
    except FileNotFoundError:
        logger.warning("Disposable baseline file missing at %s", _BASELINE_FILE)
    extra = settings.DISPOSABLE_DOMAINS_EXTRA or ""
    domains |= _parse_domains(extra.replace(",", "\n").splitlines())
    return domains


def _fetch_upstream_domains(url: str) -> set[str]:
    """Fetch and parse the upstream disposable-domains list. Raises on HTTP error."""
    with httpx.Client(timeout=30.0) as client:
        resp = client.get(url)
        resp.raise_for_status()
        return _parse_domains(resp.text.splitlines())


def refresh_disposable_domains(db: Session) -> dict:
    """(Re)populate the disposable_domains table. Intended to run from the daily cron.

    - Baseline (committed file + env) is always ensured.
    - The upstream list is merged in when reachable.
    - On upstream failure the existing table is preserved (never emptied); we only
      ensure the baseline rows are present. Returns a small summary dict.
    """
    baseline = baseline_domains()
    url = settings.DISPOSABLE_DOMAINS_URL
    remote: set[str] = set()
    upstream_ok = True

    if url:
        try:
            remote = _fetch_upstream_domains(url)
            if not remote:
                raise ValueError("upstream list was empty")
        except Exception as exc:  # noqa: BLE001 - refresh must never crash the cron
            upstream_ok = False
            logger.warning(
                "Disposable upstream fetch failed (%s); preserving table", exc
            )

    if upstream_ok:
        full = baseline | remote
        # Replace the table in one transaction: readers see the old rows until commit,
        # so there is never an empty (fail-open) window.
        db.query(DBDisposableDomain).delete()
        db.bulk_insert_mappings(
            DBDisposableDomain,
            [
                {
                    "domain": d,
                    "source": SOURCE_BASELINE if d in baseline else SOURCE_UPSTREAM,
                }
                for d in full
            ],
        )
        db.commit()
        logger.info(
            "Disposable domains refreshed: %d total (%d upstream + %d baseline)",
            len(full),
            len(remote),
            len(baseline),
        )
        return {"total": len(full), "upstream": len(remote), "baseline": len(baseline)}

    # Upstream failed: keep whatever is there, just make sure baseline is present.
    existing = {row[0] for row in db.query(DBDisposableDomain.domain).all()}
    to_add = baseline - existing
    if to_add:
        db.bulk_insert_mappings(
            DBDisposableDomain,
            [{"domain": d, "source": SOURCE_BASELINE} for d in to_add],
        )
        db.commit()
    return {"total": len(existing | baseline), "upstream": 0, "baseline": len(baseline)}


def is_blocked(db: Session, email_or_domain: str) -> bool:
    """True if the email/domain (or any parent domain) is in the disposable table."""
    if not settings.ENABLE_DISPOSABLE_EMAIL_BLOCKING:
        return False
    domain = extract_domain(email_or_domain)
    suffixes = candidate_suffixes(domain)
    if not suffixes:
        return False
    hit = (
        db.query(DBDisposableDomain.domain)
        .filter(DBDisposableDomain.domain.in_(suffixes))
        .first()
    )
    return hit is not None


def assert_email_domain_allowed(db: Session, email: str) -> None:
    """Raise 422 if the email's domain is a known disposable / dynamic-DNS domain."""
    if is_blocked(db, email):
        logger.info(
            "Blocked signup for disposable email domain: %s", extract_domain(email)
        )
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email domain.",
        )
