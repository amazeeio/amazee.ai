"""Disposable / dynamic-DNS email-domain blocking.

Defense-in-depth for the amazee.ai backend: it must not blindly trust callers
(moad, Drupal, direct API) to have filtered signup emails. This service holds an
in-memory set of blocked apex domains and matches an email's domain against it
using suffix matching, so blocking "dynv6.net" also blocks "*.dynv6.net".

Sources (merged):
  1. A committed curated baseline (app/data/disposable_domains_extra.txt) plus
     any domains from settings.DISPOSABLE_DOMAINS_EXTRA — the always-on guarantee.
  2. The upstream disposable list fetched at startup and refreshed periodically
     (settings.DISPOSABLE_DOMAINS_URL). If the fetch fails we keep the current
     set and never fall back to an empty (fail-open) set.
"""

import logging
from pathlib import Path
from typing import Iterable, Optional

import httpx
from fastapi import HTTPException, status

from app.core.config import settings

logger = logging.getLogger(__name__)

_BASELINE_FILE = Path(__file__).resolve().parent.parent / "data" / "disposable_domains_extra.txt"


def _parse_domains(lines: Iterable[str]) -> set[str]:
    domains: set[str] = set()
    for raw in lines:
        line = raw.strip().lower()
        if not line or line.startswith("#"):
            continue
        # Defend against list entries that are full emails or have a leading "@".
        if "@" in line:
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


def _candidate_suffixes(domain: str) -> list[str]:
    """Parent suffixes to test, e.g. a.b.dynv6.net -> [a.b.dynv6.net, b.dynv6.net, dynv6.net].

    Stops at the registrable-pair level (two labels); never returns a bare TLD.
    """
    labels = [p for p in domain.split(".") if p]
    if len(labels) < 2:
        return [domain] if domain else []
    return [".".join(labels[i:]) for i in range(0, len(labels) - 1)]


class DisposableDomainService:
    """Singleton holding the blocked-domain set. Reads are lock-free: refresh
    swaps in a brand-new frozenset atomically."""

    _instance: Optional["DisposableDomainService"] = None

    def __init__(self) -> None:
        self._baseline: frozenset[str] = frozenset()
        self._domains: frozenset[str] = frozenset()
        self._loaded_remote = False
        self._load_baseline()

    @classmethod
    def get_instance(cls) -> "DisposableDomainService":
        if cls._instance is None:
            cls._instance = cls()
        return cls._instance

    def _load_baseline(self) -> None:
        baseline: set[str] = set()
        try:
            baseline |= _parse_domains(_BASELINE_FILE.read_text(encoding="utf-8").splitlines())
        except FileNotFoundError:
            logger.warning("Disposable baseline file missing at %s", _BASELINE_FILE)
        extra = settings.DISPOSABLE_DOMAINS_EXTRA or ""
        baseline |= _parse_domains(extra.replace(",", "\n").splitlines())
        self._baseline = frozenset(baseline)
        # Start with at least the baseline so blocking works before the first refresh.
        if not self._domains:
            self._domains = self._baseline
        logger.info("Disposable baseline loaded: %d domains", len(self._baseline))

    async def refresh(self, client: Optional[httpx.AsyncClient] = None) -> bool:
        """Fetch the upstream list and merge with the baseline. Returns True on
        success. On failure keeps the existing set (never fail-open)."""
        url = settings.DISPOSABLE_DOMAINS_URL
        if not url:
            self._domains = self._baseline
            return True
        owns_client = client is None
        try:
            client = client or httpx.AsyncClient(timeout=15.0)
            resp = await client.get(url)
            resp.raise_for_status()
            remote = _parse_domains(resp.text.splitlines())
            if not remote:
                raise ValueError("upstream list was empty")
            self._domains = frozenset(self._baseline | remote)
            self._loaded_remote = True
            logger.info(
                "Disposable domains refreshed: %d total (%d upstream + %d baseline)",
                len(self._domains), len(remote), len(self._baseline),
            )
            return True
        except Exception as exc:  # noqa: BLE001 - never let a refresh failure break signups
            logger.warning("Disposable domain refresh failed (%s); keeping %d domains",
                           exc, len(self._domains))
            return False
        finally:
            if owns_client and client is not None:
                await client.aclose()

    def is_blocked(self, email_or_domain: str) -> bool:
        if not settings.ENABLE_DISPOSABLE_EMAIL_BLOCKING:
            return False
        domain = extract_domain(email_or_domain)
        if not domain:
            return False
        return any(suffix in self._domains for suffix in _candidate_suffixes(domain))

    @property
    def size(self) -> int:
        return len(self._domains)


def get_disposable_domain_service() -> DisposableDomainService:
    return DisposableDomainService.get_instance()


def assert_email_domain_allowed(email: str) -> None:
    """Raise 422 if the email's domain is a known disposable / dynamic-DNS domain."""
    if get_disposable_domain_service().is_blocked(email):
        logger.info("Blocked signup for disposable email domain: %s", extract_domain(email))
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail="Invalid email domain.",
        )
