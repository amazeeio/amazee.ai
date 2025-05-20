import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db.models import DBUser, DBTeam, DBProduct, DBTeamProduct
from datetime import datetime, UTC

def test_add_user_within_product_limit(client, admin_token, db, test_team, test_product):
    """Test adding a user when within product user limit"""
    # Add product to team
    team_id = test_team.id
    product_id = test_product.id
    team_product = DBTeamProduct(
        team_id=team_id,
        product_id=product_id
    )
    db.add(team_product)
    db.commit()

    # Create a user to add
    user = DBUser(
        email="newuser@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=None,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()

    # Add user to team
    response = client.post(
        f"/users/{user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": team_id}
    )
    assert response.status_code == 200
    assert response.json()["team_id"] == team_id

def test_add_user_exceeding_product_limit(client, admin_token, db, test_team, test_product):
    """Test adding a user when it would exceed product user limit"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create and add users up to the limit
    for i in range(test_product.user_count):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Try to add one more user
    new_user = DBUser(
        email="newuser@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=None,
        created_at=datetime.now(UTC)
    )
    db.add(new_user)
    db.commit()

    response = client.post(
        f"/users/{new_user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": test_team.id}
    )
    assert response.status_code == 400
    assert f"Team has reached the maximum user limit of {test_product.user_count} users" in response.json()["detail"]

def test_add_user_with_default_limit(client, admin_token, db, test_team):
    """Test adding users with default limit when team has no products"""
    # Create and add users up to the default limit (2)
    for i in range(2):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Try to add one more user
    new_user = DBUser(
        email="newuser@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=None,
        created_at=datetime.now(UTC)
    )
    db.add(new_user)
    db.commit()

    response = client.post(
        f"/users/{new_user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": test_team.id}
    )
    assert response.status_code == 400
    assert "Team has reached the default user limit of 2 users" in response.json()["detail"]

def test_add_user_with_multiple_products(client, admin_token, db, test_team):
    """Test adding users when team has multiple products with different limits"""
    # Create two products with different user limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    product2 = DBProduct(
        id="prod_test2",
        name="Test Product 2",
        user_count=5,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=1000,
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product1)
    db.add(product2)
    db.commit()

    # Add both products to team
    team_product1 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product1.id
    )
    team_product2 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product2.id
    )
    db.add(team_product1)
    db.add(team_product2)
    db.commit()

    # Create and add users up to the higher limit (5)
    for i in range(5):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Try to add one more user
    new_user = DBUser(
        email="newuser@example.com",
        hashed_password="hashed_password",
        is_active=True,
        is_admin=False,
        role="user",
        team_id=None,
        created_at=datetime.now(UTC)
    )
    db.add(new_user)
    db.commit()

    response = client.post(
        f"/users/{new_user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": test_team.id}
    )
    assert response.status_code == 400
    assert f"Team has reached the maximum user limit of {product2.user_count} users" in response.json()["detail"]
