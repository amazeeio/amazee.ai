"""Tests for GET /users/by-email endpoint."""
import pytest
from datetime import datetime, UTC

from app.db.models import DBUser, DBTeam
from app.core.security import get_password_hash
from tests.conftest import soft_delete_team_for_test


@pytest.fixture
def team_a(db):
    team = DBTeam(
        name="Team A",
        admin_email="teama@example.com",
        phone="1111111111",
        billing_address="1 A St",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@pytest.fixture
def team_b(db):
    team = DBTeam(
        name="Team B",
        admin_email="teamb@example.com",
        phone="2222222222",
        billing_address="2 B St",
        is_active=True,
        created_at=datetime.now(UTC),
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@pytest.fixture
def user_personal(db, team_a):
    user = DBUser(
        email="name+personal@gmail.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_admin=False,
        role="read_only",
        team_id=team_a.id,
        created_at=datetime.now(UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


@pytest.fixture
def user_company(db, team_b):
    user = DBUser(
        email="name+company@gmail.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_admin=False,
        role="read_only",
        team_id=team_b.id,
        created_at=datetime.now(UTC),
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def test_by_email_returns_both_variants_for_system_admin(
    client, admin_token, user_personal, user_company
):
    """Query with base email returns all team variants for a system admin."""
    response = client.get(
        "/users/by-email",
        params={"email": "name@gmail.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    emails = {u["email"] for u in data}
    assert "name+personal@gmail.com" in emails
    assert "name+company@gmail.com" in emails


def test_by_email_returns_single_variant(client, admin_token, user_personal, user_company):
    """Querying with a +suffix variant normalises and returns matching users."""
    # Normalising name+personal@gmail.com → name@gmail.com matches both variants
    response = client.get(
        "/users/by-email",
        params={"email": "name+personal@gmail.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    emails = {u["email"] for u in data}
    # Both stored users normalise to the same base — both returned
    assert "name+personal@gmail.com" in emails
    assert "name+company@gmail.com" in emails


def test_by_email_only_variant_when_unique(client, admin_token, user_personal):
    """When only one user exists with the base email, only that user is returned."""
    response = client.get(
        "/users/by-email",
        params={"email": "name+personal@gmail.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["email"] == "name+personal@gmail.com"


def test_by_email_forbidden_for_team_admin(
    client, team_admin_token, user_personal, user_company
):
    """Team admin callers receive 403."""
    response = client.get(
        "/users/by-email",
        params={"email": "name@gmail.com"},
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 403


def test_by_email_forbidden_for_regular_user(
    client, test_token, user_personal, user_company
):
    """Regular (non-admin) callers receive 403."""
    response = client.get(
        "/users/by-email",
        params={"email": "name@gmail.com"},
        headers={"Authorization": f"Bearer {test_token}"},
    )
    assert response.status_code == 403


def test_by_email_excludes_inactive_users(client, admin_token, db, team_a):
    """Inactive users are not included in results."""
    inactive = DBUser(
        email="name+inactive@gmail.com",
        hashed_password=get_password_hash("password"),
        is_active=False,
        is_admin=False,
        role="read_only",
        team_id=team_a.id,
        created_at=datetime.now(UTC),
    )
    db.add(inactive)
    db.commit()

    response = client.get(
        "/users/by-email",
        params={"email": "name@gmail.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert all(u["is_active"] for u in data)
    assert not any(u["email"] == "name+inactive@gmail.com" for u in data)


def test_by_email_excludes_users_in_deleted_teams(
    client, admin_token, db, user_personal, user_company, team_b
):
    """Users belonging to soft-deleted teams are excluded."""
    soft_delete_team_for_test(db, team_b)

    response = client.get(
        "/users/by-email",
        params={"email": "name@gmail.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    emails = {u["email"] for u in data}
    # team_b user is now inactive (soft_delete_team sets is_active=False) so excluded
    assert "name+company@gmail.com" not in emails


def test_by_email_returns_empty_list_when_no_match(client, admin_token):
    """No matching email returns an empty list, not 404."""
    response = client.get(
        "/users/by-email",
        params={"email": "nobody@nowhere.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    assert response.json() == []


def test_by_email_populates_team_name(client, admin_token, user_personal, team_a):
    """team_name is populated on each result."""
    response = client.get(
        "/users/by-email",
        params={"email": "name+personal@gmail.com"},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["team_name"] == team_a.name
