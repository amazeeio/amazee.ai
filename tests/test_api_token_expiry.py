import pytest
from datetime import datetime, timedelta, UTC
from app.db.models import DBAPIToken
from app.db.init_db import init_api_token_expiry_options


@pytest.fixture(autouse=True)
def seed_expiry_options(db):
    init_api_token_expiry_options()


def test_list_expiry_options(client, test_token):
    """Test listing expiry options"""
    response = client.get(
        "/auth/token/expiry-options",
        headers={"Authorization": f"Bearer {test_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) >= 15

    slugs = [opt["slug"] for opt in data]
    assert "1_day" in slugs
    assert "forever" in slugs


def test_create_token_with_expiry(client, test_token, test_user, db):
    """Test creating a token with specific expiry options from DB"""
    # Get options from API
    response = client.get(
        "/auth/token/expiry-options",
        headers={"Authorization": f"Bearer {test_token}"},
    )
    response.json()

    # Test a few specific ones
    test_cases = {"1_day": 1, "1_week": 7, "1_month": 30, "1_year": 365}

    for slug, days in test_cases.items():
        response = client.post(
            "/auth/token",
            headers={"Authorization": f"Bearer {test_token}"},
            json={"name": f"Test {slug}", "expiry": slug},
        )

        assert response.status_code == 200
        data = response.json()
        assert data["expiry_option"] == slug
        assert data["expires_at"] is not None

        # Verify calculated date is roughly correct (within a few seconds)
        expires_at = datetime.fromisoformat(data["expires_at"].replace("Z", "+00:00"))
        expected_expiry = datetime.now(UTC) + timedelta(days=days)
        assert abs((expires_at - expected_expiry).total_seconds()) < 5


def test_create_token_forever(client, test_token, test_user):
    """Test creating a token with 'forever' expiry"""
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"name": "Forever Token", "expiry": "forever"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["expiry_option"] == "forever"
    assert data["expires_at"] is None


def test_expired_token_access(client, test_user, db):
    """Test that an expired token cannot be used"""
    # Create an already expired token manually in DB
    expired_at = datetime.now(UTC) - timedelta(hours=1)
    db_token = DBAPIToken(
        name="Expired Token",
        token="expired-token-123",
        user_id=test_user.id,
        expires_at=expired_at,
        expiry_option="1_day",
    )
    db.add(db_token)
    db.commit()

    # Try to use the expired token
    response = client.get(
        "/auth/token", headers={"Authorization": "Bearer expired-token-123"}
    )

    assert response.status_code == 401
    assert "API token has expired" in response.json()["detail"]


def test_valid_token_access(client, test_user, db):
    """Test that a valid (not yet expired) token can be used"""
    # Create a token that expires in the future
    expires_at = datetime.now(UTC) + timedelta(days=1)
    db_token = DBAPIToken(
        name="Valid Token",
        token="valid-token-123",
        user_id=test_user.id,
        expires_at=expires_at,
        expiry_option="1_day",
    )
    db.add(db_token)
    db.commit()

    # Try to use the valid token
    response = client.get(
        "/auth/token", headers={"Authorization": "Bearer valid-token-123"}
    )

    assert response.status_code == 200

    # Verify last_used_at was updated
    db.refresh(db_token)
    assert db_token.last_used_at is not None
    assert abs((db_token.last_used_at - datetime.now(UTC)).total_seconds()) < 5
