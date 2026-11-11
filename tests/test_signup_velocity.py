"""Tests for per-IP signup velocity limiting (moad #620, Layer 2)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.db.models import DBAuditLog, DBSignupEvent
from app.services.signup_velocity import (
    client_ip,
    enforce_signup_velocity,
    prune_signup_events,
)


def _req(ip):
    return SimpleNamespace(client=SimpleNamespace(host=ip) if ip else None)


def test_client_ip():
    assert client_ip(_req("1.2.3.4")) == "1.2.3.4"
    assert client_ip(_req(None)) is None
    assert client_ip(None) is None


def test_velocity_allows_up_to_cap_then_blocks(db, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SIGNUP_VELOCITY_LIMIT", True)
    monkeypatch.setattr(settings, "SIGNUP_MAX_PER_IP_PER_WINDOW", 2)
    monkeypatch.setattr(settings, "SIGNUP_VELOCITY_WINDOW_MINUTES", 60)
    req = _req("9.9.9.9")

    # cap=2 -> first two pass, third is blocked
    enforce_signup_velocity(req, db, email="a@x.com", endpoint="t")
    enforce_signup_velocity(req, db, email="b@x.com", endpoint="t")
    with pytest.raises(HTTPException) as exc:
        enforce_signup_velocity(req, db, email="c@x.com", endpoint="t")
    assert exc.value.status_code == 429

    # all attempts (including the blocked one) are recorded
    assert (
        db.query(DBSignupEvent).filter(DBSignupEvent.ip_address == "9.9.9.9").count()
        == 3
    )


def test_velocity_is_per_ip(db, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SIGNUP_VELOCITY_LIMIT", True)
    monkeypatch.setattr(settings, "SIGNUP_MAX_PER_IP_PER_WINDOW", 1)
    # one IP hits its cap
    enforce_signup_velocity(_req("10.0.0.1"), db, endpoint="t")
    with pytest.raises(HTTPException):
        enforce_signup_velocity(_req("10.0.0.1"), db, endpoint="t")
    # a different IP is unaffected
    enforce_signup_velocity(_req("10.0.0.2"), db, endpoint="t")


def test_velocity_disabled_is_noop(db, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SIGNUP_VELOCITY_LIMIT", False)
    monkeypatch.setattr(settings, "SIGNUP_MAX_PER_IP_PER_WINDOW", 1)
    req = _req("8.8.8.8")
    for _ in range(5):
        enforce_signup_velocity(req, db, endpoint="t")  # never raises
    assert (
        db.query(DBSignupEvent).filter(DBSignupEvent.ip_address == "8.8.8.8").count()
        == 0
    )


def test_velocity_no_ip_is_not_limited(db, monkeypatch):
    monkeypatch.setattr(settings, "ENABLE_SIGNUP_VELOCITY_LIMIT", True)
    monkeypatch.setattr(settings, "SIGNUP_MAX_PER_IP_PER_WINDOW", 1)
    for _ in range(3):
        enforce_signup_velocity(
            _req(None), db, endpoint="t"
        )  # unknown IP: skip, never raise
    assert db.query(DBSignupEvent).count() == 0


@pytest.mark.parametrize("expired", [True, False])
def test_prune_removes_only_old_rows(db, monkeypatch, expired):
    monkeypatch.setattr(settings, "SIGNUP_EVENTS_RETENTION_DAYS", 7)
    now = datetime.now(UTC)
    fresh = DBSignupEvent(ip_address="1.1.1.1", endpoint="t")
    fresh.created_at = now - timedelta(days=1)
    db.add(fresh)
    if expired:
        old = DBSignupEvent(ip_address="2.2.2.2", endpoint="t")
        old.created_at = now - timedelta(days=10)
        db.add(old)
    db.commit()

    deleted = prune_signup_events(db)

    assert deleted == (1 if expired else 0)
    remaining = db.query(DBSignupEvent).all()
    assert len(remaining) == 1
    assert remaining[0].ip_address == "1.1.1.1"

    audit_rows = db.query(DBAuditLog).all()
    assert len(audit_rows) == 1
    row = audit_rows[0]
    assert row.action == "signup_events.prune"
    assert row.event_type == "WORKER"
    assert row.resource_type == "signup_events"
    assert row.user_id is None
    assert row.resource_id is None
    assert row.request_source is None
    assert row.details["rows_deleted"] == deleted
    assert row.details["retention_days"] == 7
    cutoff = datetime.fromisoformat(row.details["cutoff"])
    assert cutoff.utcoffset() == timedelta(0)
    assert abs(cutoff - (now - timedelta(days=7))) < timedelta(seconds=5)
    # Only these keys, so no IP address or email gets into the row.
    assert set(row.details) == {"rows_deleted", "cutoff", "retention_days"}
