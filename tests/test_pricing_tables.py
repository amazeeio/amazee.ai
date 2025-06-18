import pytest
from fastapi import status
from datetime import datetime, UTC
from app.db.models import DBSystemSecret, DBUser
from app.core.security import get_password_hash

def test_create_pricing_table_system_admin(client, db, admin_token):
    """Test that a system admin can create a pricing table"""
    # Create pricing table
    response = client.post(
        "/pricing-tables",
        json={"pricing_table_id": "test_pricing_table_123"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "test_pricing_table_123"
    assert "updated_at" in data

    # Verify in database
    pricing_table = db.query(DBSystemSecret).filter(
        DBSystemSecret.key == "CurrentPricingTable"
    ).first()
    assert pricing_table is not None
    assert pricing_table.value == "test_pricing_table_123"

def test_create_pricing_table_team_admin(client, db, test_team_admin):
    """Test that a team admin cannot create a pricing table"""
    # Login as team admin
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to create pricing table
    response = client.post(
        "/pricing-tables",
        json={"pricing_table_id": "test_pricing_table_123"},
        headers=headers
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN

def test_get_pricing_table_team_admin(client, db, team_admin_token):
    """Test that a team admin can get the pricing table"""
    # Create pricing table as system admin
    system_admin = DBUser(
        email="system_admin@test.com",
        hashed_password=get_password_hash("testpassword"),
        is_admin=True
    )
    db.add(system_admin)
    db.commit()

    # Create pricing table
    pricing_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="test_pricing_table_123",
        description="Test pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Get pricing table
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "test_pricing_table_123"
    assert "updated_at" in data

def test_get_pricing_table_not_found(client, db, team_admin_token):
    """Test getting pricing table when none exists"""
    # Try to get pricing table
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_404_NOT_FOUND

def test_delete_pricing_table_system_admin(client, db, test_admin):
    """Test that a system admin can delete the pricing table"""
    # Create pricing table
    pricing_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="test_pricing_table_123",
        description="Test pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Login as system admin
    response = client.post(
        "/auth/login",
        data={"username": test_admin.email, "password": "adminpassword"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Delete pricing table
    response = client.delete("/pricing-tables", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["message"] == "Pricing table deleted successfully"

    # Verify deleted from database
    pricing_table = db.query(DBSystemSecret).filter(
        DBSystemSecret.key == "CurrentPricingTable"
    ).first()
    assert pricing_table is None

def test_delete_pricing_table_team_admin(client, db, test_team_admin):
    """Test that a team admin cannot delete the pricing table"""
    # Create pricing table
    pricing_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="test_pricing_table_123",
        description="Test pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Login as team admin
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to delete pricing table
    response = client.delete("/pricing-tables", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN

def test_update_existing_pricing_table(client, db, test_admin):
    """Test updating an existing pricing table"""
    # Create initial pricing table
    pricing_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="initial_pricing_table",
        description="Test pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Login as system admin
    response = client.post(
        "/auth/login",
        data={"username": test_admin.email, "password": "adminpassword"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Update pricing table
    response = client.post(
        "/pricing-tables",
        json={"pricing_table_id": "updated_pricing_table"},
        headers=headers
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "updated_pricing_table"
    assert "updated_at" in data

    # Verify only one pricing table exists with updated value
    pricing_tables = db.query(DBSystemSecret).filter(
        DBSystemSecret.key == "CurrentPricingTable"
    ).all()
    assert len(pricing_tables) == 1
    assert pricing_tables[0].value == "updated_pricing_table"

def test_key_creator_cannot_access_pricing_table(client, db, test_team_key_creator):
    """Test that a key_creator cannot access the pricing table"""
    # Create pricing table as system admin
    pricing_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="test_pricing_table_123",
        description="Test pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Login as key_creator
    response = client.post(
        "/auth/login",
        data={"username": test_team_key_creator.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to get pricing table
    response = client.get("/pricing-tables", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN

    # Try to create pricing table
    response = client.post(
        "/pricing-tables",
        json={"pricing_table_id": "new_pricing_table"},
        headers=headers
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN

    # Try to delete pricing table
    response = client.delete("/pricing-tables", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN

def test_create_always_free_pricing_table(client, db, admin_token):
    """Test that a system admin can create an always-free pricing table"""
    # Create always-free pricing table
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "test_always_free_table_123",
            "table_type": "always_free"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "test_always_free_table_123"
    assert "updated_at" in data

    # Verify in database
    pricing_table = db.query(DBSystemSecret).filter(
        DBSystemSecret.key == "AlwaysFreePricingTable"
    ).first()
    assert pricing_table is not None
    assert pricing_table.value == "test_always_free_table_123"

def test_get_pricing_table_always_free_team(client, db, team_admin_token, test_team):
    """Test that an always-free team gets the always-free pricing table"""
    # Set team as always-free
    test_team.is_always_free = True
    db.add(test_team)
    db.commit()

    # Create both pricing tables
    standard_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="standard_table_123",
        description="Standard pricing table",
        created_at=datetime.now(UTC)
    )
    always_free_table = DBSystemSecret(
        key="AlwaysFreePricingTable",
        value="always_free_table_123",
        description="Always-free pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.add(always_free_table)
    db.commit()

    # Get pricing table
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "always_free_table_123"
    assert "updated_at" in data

def test_get_pricing_table_always_free_not_found(client, db, team_admin_token, test_team):
    """Test getting pricing table when team is always-free but no always-free table exists"""
    # Set team as always-free
    test_team.is_always_free = True
    db.add(test_team)
    db.commit()

    # Create only standard pricing table
    standard_table = DBSystemSecret(
        key="CurrentPricingTable",
        value="standard_table_123",
        description="Standard pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.commit()

    # Try to get pricing table
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Pricing table ID not found"

def test_update_always_free_pricing_table(client, db, admin_token):
    """Test updating an existing always-free pricing table"""
    # Create initial always-free pricing table
    pricing_table = DBSystemSecret(
        key="AlwaysFreePricingTable",
        value="initial_always_free_table",
        description="Test always-free pricing table",
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Update always-free pricing table
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "updated_always_free_table",
            "table_type": "always_free"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "updated_always_free_table"
    assert "updated_at" in data

    # Verify only one always-free pricing table exists with updated value
    pricing_tables = db.query(DBSystemSecret).filter(
        DBSystemSecret.key == "AlwaysFreePricingTable"
    ).all()
    assert len(pricing_tables) == 1
    assert pricing_tables[0].value == "updated_always_free_table"

def test_create_always_free_pricing_table_team_admin(client, db, test_team_admin):
    """Test that a team admin cannot create an always-free pricing table"""
    # Login as team admin
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to create always-free pricing table
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "test_always_free_table_123",
            "table_type": "always_free"
        },
        headers=headers
    )
    assert response.status_code == status.HTTP_403_FORBIDDEN

def test_create_pricing_table_invalid_type(client, db, admin_token):
    """Test that a system admin cannot create a pricing table with an invalid type"""
    # Try to create pricing table with invalid type
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "test_table_123",
            "table_type": "invalid_type"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_422_UNPROCESSABLE_ENTITY
    assert "table_type" in response.json()["detail"][0]["loc"]
    assert "Input should be 'standard' or 'always_free'" in response.json()["detail"][0]["msg"]