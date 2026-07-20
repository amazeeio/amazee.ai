"""Tests for disposable / dynamic-DNS email-domain blocking (moad #620, Layer 1)."""

import pytest

from app.core.config import settings
from app.services.disposable_domains import (
    DisposableDomainService,
    _candidate_suffixes,
    assert_email_domain_allowed,
    extract_domain,
    get_disposable_domain_service,
)
from fastapi import HTTPException


def test_extract_domain():
    assert extract_domain("Alice@B.DYNV6.net") == "b.dynv6.net"
    assert extract_domain("dynv6.net") == "dynv6.net"
    assert extract_domain("user+12938@sub.example.com") == "sub.example.com"
    assert extract_domain("  x@y.com. ") == "y.com"
    assert extract_domain("") == ""


def test_candidate_suffixes():
    assert _candidate_suffixes("a.b.dynv6.net") == [
        "a.b.dynv6.net",
        "b.dynv6.net",
        "dynv6.net",
    ]
    assert _candidate_suffixes("dynv6.net") == ["dynv6.net"]
    assert _candidate_suffixes("localhost") == ["localhost"]
    assert _candidate_suffixes("") == []


def test_is_blocked_subdomain_matching():
    svc = DisposableDomainService()
    svc._domains = frozenset({"dynv6.net"})
    # apex and any subdomain depth
    assert svc.is_blocked("x@dynv6.net")
    assert svc.is_blocked("x@msn-mail-free-6326.dynv6.net")
    assert svc.is_blocked("x@a.b.c.dynv6.net")
    # label-boundary matching: a look-alike apex must NOT match
    assert not svc.is_blocked("x@notdynv6.net")
    assert not svc.is_blocked("x@dynv6.net.evil.com")
    assert not svc.is_blocked("x@example.com")


def test_committed_baseline_blocks_known_abusers():
    svc = get_disposable_domain_service()
    assert svc.is_blocked("bot@sub.dynv6.net")
    assert svc.is_blocked("bot@vip.baileybridge.org")
    assert svc.is_blocked("bot@wzry.ntfdhujik.kozow.com")
    # a normal domain is allowed
    assert not svc.is_blocked("real.person@gmail.com")


def test_blocking_can_be_disabled(monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_DISPOSABLE_EMAIL_BLOCKING", False)
    svc = DisposableDomainService()
    svc._domains = frozenset({"dynv6.net"})
    assert not svc.is_blocked("x@dynv6.net")


def test_assert_email_domain_allowed_raises_422():
    with pytest.raises(HTTPException) as exc:
        assert_email_domain_allowed("bot@sub.dynv6.net")
    assert exc.value.status_code == 422
    assert exc.value.detail == "Invalid email domain."
    # allowed domain does not raise
    assert_email_domain_allowed("real@gmail.com")


def test_validate_email_endpoint_blocks_disposable_domain(client):
    resp = client.post(
        "/auth/validate-email",
        json={"email": "bot@msn-mail-free-6326.dynv6.net"},
    )
    assert resp.status_code == 422
    assert resp.json()["detail"] == "Invalid email domain."
