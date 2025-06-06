import pytest
from fastapi import status
from datetime import datetime, UTC
from app.db.models import DBSystemSecret, DBUser
from app.core.security import get_password_hash

def test_create_pricing_table_system_admin(client, db, test_admin):
    """Test that a system admin can create a pricing table"""
    # Login as system admin
    response = client.post(
        "/auth/login",
        data={"username": test_admin.email, "password": "adminpassword"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Create pricing table
    response = client.post(
        "/pricing-tables",
        json={"pricing_table_id": "test_pricing_table_123"},
        headers=headers
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

def test_get_pricing_table_team_admin(client, db, test_team_admin):
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

    # Login as team admin
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Get pricing table
    response = client.get("/pricing-tables", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "test_pricing_table_123"
    assert "updated_at" in data

def test_get_pricing_table_not_found(client, db, test_team_admin):
    """Test getting pricing table when none exists"""
    # Login as team admin
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to get pricing table
    response = client.get("/pricing-tables", headers=headers)
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