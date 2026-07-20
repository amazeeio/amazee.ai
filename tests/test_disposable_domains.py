"""Tests for disposable / dynamic-DNS email-domain blocking (moad #620, Layer 1).

The blocklist lives in the disposable_domains table, populated by
refresh_disposable_domains() and cross-checked (with subdomain matching) at signup.
Tests set DISPOSABLE_DOMAINS_URL="" (conftest) so refresh uses only the committed
baseline unless a test monkeypatches the upstream fetch.
"""

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.db.models import DBDisposableDomain
from app.services import disposable_domains as dd
from app.services.disposable_domains import (
    assert_email_domain_allowed,
    candidate_suffixes,
    extract_domain,
    is_blocked,
    refresh_disposable_domains,
)


def test_extract_domain():
    assert extract_domain("Alice@B.DYNV6.net") == "b.dynv6.net"
    assert extract_domain("dynv6.net") == "dynv6.net"
    assert extract_domain("user+12938@sub.example.com") == "sub.example.com"
    assert extract_domain("  x@y.com. ") == "y.com"
    assert extract_domain("") == ""


def test_candidate_suffixes():
    assert candidate_suffixes("a.b.dynv6.net") == [
        "a.b.dynv6.net",
        "b.dynv6.net",
        "dynv6.net",
    ]
    assert candidate_suffixes("dynv6.net") == ["dynv6.net"]
    assert candidate_suffixes("localhost") == ["localhost"]
    assert candidate_suffixes("") == []


def test_refresh_seeds_baseline_into_db(db):
    summary = refresh_disposable_domains(db)  # URL="" -> baseline only
    assert summary["baseline"] > 0
    rows = {r[0] for r in db.query(DBDisposableDomain.domain).all()}
    assert "dynv6.net" in rows
    assert "kozow.com" in rows
    assert "baileybridge.org" in rows


def test_is_blocked_subdomain_matching(db):
    refresh_disposable_domains(db)  # seeds baseline (incl. dynv6.net)
    # apex and any subdomain depth are blocked
    assert is_blocked(db, "x@dynv6.net")
    assert is_blocked(db, "x@msn-mail-free-6326.dynv6.net")
    assert is_blocked(db, "x@a.b.c.dynv6.net")
    # label-boundary matching: look-alikes must NOT match
    assert not is_blocked(db, "x@notdynv6.net")
    assert not is_blocked(db, "x@dynv6.net.evil.com")
    assert not is_blocked(db, "real.person@gmail.com")


def test_refresh_merges_upstream(db, monkeypatch):
    monkeypatch.setattr(settings, "DISPOSABLE_DOMAINS_URL", "http://upstream.test/list")
    monkeypatch.setattr(
        dd, "_fetch_upstream_domains", lambda url: {"foo-disposable.test"}
    )
    summary = refresh_disposable_domains(db)
    assert summary["upstream"] == 1
    # both the upstream entry (and its subdomains) and the baseline are blocked
    assert is_blocked(db, "a@sub.foo-disposable.test")
    assert is_blocked(db, "a@dynv6.net")


def test_refresh_preserves_table_on_upstream_failure(db, monkeypatch):
    refresh_disposable_domains(db)  # seed baseline first
    before = db.query(DBDisposableDomain).count()

    def boom(url):
        raise RuntimeError("network down")

    monkeypatch.setattr(settings, "DISPOSABLE_DOMAINS_URL", "http://upstream.test/list")
    monkeypatch.setattr(dd, "_fetch_upstream_domains", boom)
    refresh_disposable_domains(db)  # must not wipe the table
    assert db.query(DBDisposableDomain).count() >= before
    assert is_blocked(db, "a@dynv6.net")


def test_assert_email_domain_allowed(db):
    refresh_disposable_domains(db)
    with pytest.raises(HTTPException) as exc:
        assert_email_domain_allowed(db, "bot@sub.dynv6.net")
    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid email domain."
    # allowed domain does not raise
    assert_email_domain_allowed(db, "real@gmail.com")


def test_blocking_can_be_disabled(db, monkeypatch):
    refresh_disposable_domains(db)
    monkeypatch.setattr(settings, "ENABLE_DISPOSABLE_EMAIL_BLOCKING", False)
    assert not is_blocked(db, "x@dynv6.net")


def test_validate_email_endpoint_blocks_disposable_domain(client, db):
    refresh_disposable_domains(db)  # same session the endpoint uses (see conftest)
    resp = client.post(
        "/auth/validate-email",
        json={"email": "bot@msn-mail-free-6326.dynv6.net"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Invalid email domain."
