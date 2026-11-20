"""Tests for per-IP signup velocity limiting (moad #620, Layer 2)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from unittest.mock import patch

import pytest
from fastapi import HTTPException
from sqlalchemy import event

from app.core.config import settings
from app.db.models import DBAuditLog, DBSignupEvent, DBSystemSecret, DBTeam, DBUser
from app.services.signup_velocity import (
    registrable_domain,
    client_ip,
    enforce_mailbox_cap,
    enforce_provision_caps,
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


def test_prune_keeps_old_rows_when_the_audit_row_fails(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_EVENTS_RETENTION_DAYS", 7)
    old = DBSignupEvent(ip_address="2.2.2.2", endpoint="t")
    old.created_at = datetime.now(UTC) - timedelta(days=10)
    db.add(old)
    db.commit()

    # Fails inside the flush that commit() runs, where a real insert error fails.
    def fail_insert(*_):
        raise RuntimeError("audit insert failed")

    event.listen(DBAuditLog, "before_insert", fail_insert)
    try:
        with pytest.raises(RuntimeError, match="audit insert failed"):
            prune_signup_events(db)
    finally:
        event.remove(DBAuditLog, "before_insert", fail_insert)
    db.rollback()

    assert db.query(DBSignupEvent).count() == 1
    assert db.query(DBAuditLog).count() == 0


def test_prune_script_rolls_back_and_releases_the_lock_on_failure(db, monkeypatch):
    from scripts import trigger_prune_signup_events_job as job

    monkeypatch.setattr(settings, "SIGNUP_EVENTS_RETENTION_DAYS", 7)
    old = DBSignupEvent(ip_address="2.2.2.2", endpoint="t")
    old.created_at = datetime.now(UTC) - timedelta(days=10)
    db.add(old)
    db.commit()

    def fail_insert(*_):
        raise RuntimeError("audit insert failed")

    event.listen(DBAuditLog, "before_insert", fail_insert)
    try:
        with pytest.raises(SystemExit) as exit_info:
            job.main()
    finally:
        event.remove(DBAuditLog, "before_insert", fail_insert)

    assert exit_info.value.code == 1
    assert db.query(DBSignupEvent).count() == 1
    assert db.query(DBAuditLog).count() == 0
    lock = db.query(DBSystemSecret).filter_by(key=f"lock_{job.LOCK_NAME}").one()
    assert lock.value == "false"


def test_provision_caps_per_domain_exempt_and_global(db, monkeypatch):
    """Bots: one fresh domain, rotating IPs. Domain cap blocks it, free-mail
    is exempt from that cap, and the global cap backstops domain rotation."""
    monkeypatch.setattr(settings, "PROVISION_MAX_PER_DOMAIN_PER_DAY", 2)
    monkeypatch.setattr(settings, "PROVISION_MAX_PER_HOUR", 5)
    monkeypatch.setattr(settings, "PROVISION_DOMAIN_CAP_EXEMPT", "gmail.com")

    enforce_provision_caps(db, "a@bot.store")
    enforce_provision_caps(db, "b@bot.store")
    with pytest.raises(HTTPException) as exc:
        enforce_provision_caps(db, "c@bot.store")
    assert exc.value.status_code == 429
    # a lookalike domain is not caught by the bot.store LIKE
    enforce_provision_caps(db, "a@notbot.store")

    # exempt domain passes the domain cap (3 admitted rows so far); the
    # global cap of 5 then blocks the third gmail attempt
    enforce_provision_caps(db, "x@gmail.com")
    enforce_provision_caps(db, "y@gmail.com")
    with pytest.raises(HTTPException):
        enforce_provision_caps(db, "z@gmail.com")

    # rejected attempts are not recorded, so they can't hold the window full
    assert (
        db.query(DBSignupEvent)
        .filter(DBSignupEvent.endpoint == "provision-key")
        .count()
        == 5
    )


def test_provision_domain_cap_groups_subdomains(db, monkeypatch):
    """Sibling subdomains count against one registrable domain."""
    monkeypatch.setattr(settings, "PROVISION_MAX_PER_DOMAIN_PER_DAY", 2)
    monkeypatch.setattr(settings, "PROVISION_MAX_PER_HOUR", 0)

    # A look-alike domain must not count against bot.store.
    enforce_provision_caps(db, "z@notbot.store")
    enforce_provision_caps(db, "z@x.notbot.store")
    enforce_provision_caps(db, "a@x.bot.store")
    enforce_provision_caps(db, "b@bot.store")
    with pytest.raises(HTTPException):
        enforce_provision_caps(db, "c@y.bot.store")

    assert registrable_domain("a.b.bot.store") == "bot.store"
    assert registrable_domain("mail.example.co.uk") == "example.co.uk"
    assert registrable_domain("a.b.co.uk") == "b.co.uk"
    assert registrable_domain("a.bot.io") == "bot.io"
    assert registrable_domain("bot.store") == "bot.store"


def _users(db, *emails, team=True):
    for email in emails:
        team_id = None
        if team:
            team_row = DBTeam(name=email, admin_email=email)
            db.add(team_row)
            db.flush()
            team_id = team_row.id
        db.add(
            DBUser(email=email, hashed_password="x", is_active=True, team_id=team_id)
        )
    db.commit()


def _blocked(db, email, **kwargs):
    with pytest.raises(HTTPException) as exc:
        enforce_mailbox_cap(db, email, **kwargs)
    assert exc.value.status_code == 429


def test_mailbox_cap_blocks_dot_variants_and_allows_same_address(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "a.b@gmail.com")
    _blocked(db, "ab@gmail.com")
    _blocked(db, "ab+x@googlemail.com")
    for email in ("a.b@gmail.com", "A.B@gmail.com", "a.b+tag@gmail.com"):
        enforce_mailbox_cap(db, email)


def test_mailbox_cap_existing_not_ok_counts_other_addresses(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "a.b@gmail.com")
    enforce_mailbox_cap(db, "a.b@gmail.com", existing_ok=False)
    _users(db, "ab@gmail.com")
    _blocked(db, "a.b@gmail.com", existing_ok=False)


def test_mailbox_cap_ignores_portal_surrogate_users(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "a.b@gmail.com", "a.b+123@gmail.com")
    enforce_mailbox_cap(db, "a.b@gmail.com", existing_ok=False)


def test_mailbox_cap_ignores_portal_team_tags(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "john.doe@gmail.com", "john.doe+123@gmail.com", "john.doe+456@gmail.com")
    enforce_mailbox_cap(db, "john.doe@gmail.com")
    enforce_mailbox_cap(db, "john.doe@gmail.com", existing_ok=False)


def test_mailbox_cap_ignores_a_team_tag_on_a_tagged_address(db, monkeypatch):
    # Everything from the first "+" is removed, so both rows are one address.
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "a+x@gmail.com", "a+x+789@gmail.com")
    enforce_mailbox_cap(db, "a@gmail.com")
    enforce_mailbox_cap(db, "a@gmail.com", existing_ok=False)


def test_mailbox_cap_counts_a_tagged_dot_variant(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "john.doe@gmail.com", "johndoe+123@gmail.com")
    _blocked(db, "john.doe@gmail.com", existing_ok=False)


def test_mailbox_cap_ignores_case(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "John.Doe@Gmail.com", "john.doe+5@gmail.com")
    enforce_mailbox_cap(db, "john.doe@gmail.com", existing_ok=False)


def test_mailbox_cap_ignores_team_less_users(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "j.ohndoe@gmail.com", team=False)
    enforce_mailbox_cap(db, "johndoe@gmail.com")
    enforce_mailbox_cap(db, "johndoe@gmail.com", existing_ok=False)


def test_mailbox_cap_skips_other_domains_and_mailboxes(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _users(db, "a.b@x.com", "a.b@gmail.com")
    enforce_mailbox_cap(db, "ab@x.com")
    enforce_mailbox_cap(db, "a.c@gmail.com")


def test_mailbox_cap_zero_disables(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 0)
    _users(db, "a.b@gmail.com")
    enforce_mailbox_cap(db, "ab@gmail.com")


def test_mailbox_cap_of_two_allows_a_second_address(db, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 2)
    _users(db, "a.b@gmail.com")
    enforce_mailbox_cap(db, "ab@gmail.com")
    _users(db, "ab@gmail.com")
    _blocked(db, "a.b@googlemail.com")


@pytest.fixture
def auth_mocks():
    with (
        patch("app.api.auth.DynamoDBService") as dynamodb,
        patch("app.api.auth.SESService") as ses,
    ):
        yield dynamodb.return_value, ses.return_value


def _sign_in(client, dynamodb, email):
    dynamodb.read_validation_code.return_value = {"email": email, "code": "TESTCODE"}
    return client.post(
        "/auth/sign-in", json={"username": email, "verification_code": "TESTCODE"}
    )


def test_mailbox_cap_on_validate_email(client, db, auth_mocks, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    _, ses = auth_mocks
    _users(db, "a.b@gmail.com")

    response = client.post("/auth/validate-email", json={"email": "ab@gmail.com"})
    assert response.status_code == 429
    ses.send_email.assert_not_called()

    for email in ("a.b@gmail.com", "a.b+x@gmail.com"):
        response = client.post("/auth/validate-email", json={"email": email})
        assert response.status_code == 200


def test_mailbox_cap_on_sign_in_of_a_new_address(client, db, auth_mocks, monkeypatch):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    dynamodb, _ = auth_mocks
    _users(db, "a.b@gmail.com")

    assert _sign_in(client, dynamodb, "ab@gmail.com").status_code == 429
    assert db.query(DBUser).filter(DBUser.email == "ab@gmail.com").first() is None


def test_mailbox_cap_allows_sign_in_of_an_existing_user(
    client, db, auth_mocks, monkeypatch
):
    monkeypatch.setattr(settings, "SIGNUP_MAX_ADDRESSES_PER_MAILBOX", 1)
    dynamodb, _ = auth_mocks
    _users(db, "a.b@gmail.com")
    assert _sign_in(client, dynamodb, "a.b@gmail.com").status_code == 200

    # A second address from before the cap must not lock out the first one.
    _users(db, "ab@gmail.com")
    assert _sign_in(client, dynamodb, "a.b@gmail.com").status_code == 200
