"""Tests for per-IP signup velocity limiting (moad #620, Layer 2)."""

from types import SimpleNamespace

import pytest
from fastapi import HTTPException

from app.core.config import settings
from app.db.models import DBSignupEvent
from app.services.signup_velocity import client_ip, enforce_signup_velocity


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
