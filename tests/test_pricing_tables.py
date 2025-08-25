import pytest
from fastapi import status
from datetime import datetime, UTC
from app.db.models import DBUser, DBPricingTable
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
    assert data["stripe_publishable_key"] == "pk_test_string"  # Default from settings
    assert "updated_at" in data

    # Verify in database
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "standard",
        DBPricingTable.is_active == True
    ).first()
    assert pricing_table is not None
    assert pricing_table.pricing_table_id == "test_pricing_table_123"
    assert pricing_table.stripe_publishable_key == "pk_test_string"

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
    pricing_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="test_pricing_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Get pricing table (should default to standard for non-always-free team)
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "test_pricing_table_123"
    assert data["stripe_publishable_key"] == "pk_test_string"
    assert "updated_at" in data

def test_get_pricing_table_not_found(client, db, team_admin_token):
    """Test getting pricing table when none exists"""
    # Try to get pricing table
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_404_NOT_FOUND

def test_delete_pricing_table_system_admin(client, db, test_admin):
    """Test that a system admin can delete the pricing table"""
    # Create pricing table
    pricing_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="test_pricing_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
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
    response = client.delete("/pricing-tables?table_type=standard", headers=headers)
    assert response.status_code == status.HTTP_200_OK
    assert response.json()["message"] == "Pricing table of type 'standard' deleted successfully"

    # Verify soft deleted from database (is_active = False)
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "standard",
        DBPricingTable.is_active == True
    ).first()
    assert pricing_table is None

def test_delete_pricing_table_team_admin(client, db, test_team_admin):
    """Test that a team admin cannot delete the pricing table"""
    # Create pricing table
    pricing_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="test_pricing_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
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
    response = client.delete("/pricing-tables?table_type=standard", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN

def test_update_existing_pricing_table(client, db, test_admin):
    """Test updating an existing pricing table"""
    # Create initial pricing table
    pricing_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="initial_pricing_table",
        stripe_publishable_key="pk_test_string",
        is_active=True,
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
    assert data["stripe_publishable_key"] == "pk_test_string"
    assert "updated_at" in data

    # Verify only one pricing table exists with updated value
    pricing_tables = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "standard",
        DBPricingTable.is_active == True
    ).all()
    assert len(pricing_tables) == 1
    assert pricing_tables[0].pricing_table_id == "updated_pricing_table"

def test_key_creator_cannot_access_pricing_table(client, db, test_team_key_creator):
    """Test that a key_creator cannot access the pricing table"""
    # Create pricing table as system admin
    pricing_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="test_pricing_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
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
    response = client.delete("/pricing-tables?table_type=standard", headers=headers)
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
    assert data["stripe_publishable_key"] == "pk_test_string"  # Default from settings
    assert "updated_at" in data

    # Verify in database
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "always_free",
        DBPricingTable.is_active == True
    ).first()
    assert pricing_table is not None
    assert pricing_table.pricing_table_id == "test_always_free_table_123"
    assert pricing_table.stripe_publishable_key == "pk_test_string"

def test_create_gpt_pricing_table(client, db, admin_token):
    """Test that a system admin can create a gpt pricing table"""
    # Create gpt pricing table
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "test_gpt_table_123",
            "table_type": "gpt"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "test_gpt_table_123"
    assert data["stripe_publishable_key"] == "pk_test_string"  # Default from settings
    assert "updated_at" in data

    # Verify in database
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "gpt",
        DBPricingTable.is_active == True
    ).first()
    assert pricing_table is not None
    assert pricing_table.pricing_table_id == "test_gpt_table_123"
    assert pricing_table.stripe_publishable_key == "pk_test_string"

def test_get_pricing_table_always_free_team(client, db, team_admin_token, test_team):
    """Test that an always-free team gets the always-free pricing table"""
    # Set team as always-free
    test_team.is_always_free = True
    db.add(test_team)
    db.commit()

    # Create both pricing tables
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    always_free_table = DBPricingTable(
        table_type="always_free",
        pricing_table_id="always_free_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.add(always_free_table)
    db.commit()

    # Get pricing table (should default to always_free for always-free team)
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "always_free_table_123"
    assert data["stripe_publishable_key"] == "pk_test_string"
    assert "updated_at" in data

def test_get_pricing_table_always_free_not_found(client, db, team_admin_token, test_team):
    """Test getting pricing table when team is always-free but no always-free table exists"""
    # Set team as always-free
    test_team.is_always_free = True
    db.add(test_team)
    db.commit()

    # Create only standard pricing table
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.commit()

    # Try to get pricing table
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_404_NOT_FOUND
    assert response.json()["detail"] == "Pricing table of type 'always_free' not found"

def test_update_always_free_pricing_table(client, db, admin_token):
    """Test updating an existing always-free pricing table"""
    # Create initial always-free pricing table
    pricing_table = DBPricingTable(
        table_type="always_free",
        pricing_table_id="initial_always_free_table",
        stripe_publishable_key="pk_test_string",
        is_active=True,
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
    assert data["stripe_publishable_key"] == "pk_test_string"
    assert "updated_at" in data

    # Verify only one always-free pricing table exists with updated value
    pricing_tables = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "always_free",
        DBPricingTable.is_active == True
    ).all()
    assert len(pricing_tables) == 1
    assert pricing_tables[0].pricing_table_id == "updated_always_free_table"

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
    assert "Input should be 'standard', 'always_free' or 'gpt'" in response.json()["detail"][0]["msg"]

def test_create_pricing_table_without_publishable_key(client, db, admin_token):
    """Test that a system admin can create a pricing table without providing stripe_publishable_key"""
    # Create pricing table without stripe_publishable_key
    response = client.post(
        "/pricing-tables",
        json={"pricing_table_id": "test_pricing_table_123"},
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "test_pricing_table_123"
    assert data["stripe_publishable_key"] == "pk_test_string"  # Should use system default
    assert "updated_at" in data

def test_create_pricing_table_with_custom_publishable_key(client, db, admin_token):
    """Test that a system admin can create a pricing table with custom stripe_publishable_key"""
    # Create pricing table with custom stripe_publishable_key
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "test_pricing_table_123",
            "stripe_publishable_key": "pk_custom_key_123"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "test_pricing_table_123"
    assert data["stripe_publishable_key"] == "pk_custom_key_123"  # Should use provided key
    assert "updated_at" in data

def test_update_pricing_table_with_custom_publishable_key(client, db, admin_token):
    """Test that a system admin can update a pricing table with custom stripe_publishable_key"""
    # Create initial pricing table
    pricing_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="initial_pricing_table",
        stripe_publishable_key="pk_initial_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(pricing_table)
    db.commit()

    # Update pricing table with custom stripe_publishable_key
    response = client.post(
        "/pricing-tables",
        json={
            "pricing_table_id": "updated_pricing_table",
            "stripe_publishable_key": "pk_updated_key"
        },
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == status.HTTP_201_CREATED
    data = response.json()
    assert data["pricing_table_id"] == "updated_pricing_table"
    assert data["stripe_publishable_key"] == "pk_updated_key"
    assert "updated_at" in data

    # Verify in database
    pricing_table = db.query(DBPricingTable).filter(
        DBPricingTable.table_type == "standard",
        DBPricingTable.is_active == True
    ).first()
    assert pricing_table.pricing_table_id == "updated_pricing_table"
    assert pricing_table.stripe_publishable_key == "pk_updated_key"

def test_get_all_pricing_tables_system_admin(client, db, admin_token):
    """Test that a system admin can get all pricing tables"""
    # Create both pricing tables
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_standard_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    always_free_table = DBPricingTable(
        table_type="always_free",
        pricing_table_id="always_free_table_123",
        stripe_publishable_key="pk_always_free_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.add(always_free_table)
    db.commit()

    # Get all pricing tables
    response = client.get("/pricing-tables/list", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert "tables" in data
    assert "standard" in data["tables"]
    assert data["tables"]["standard"]["pricing_table_id"] == "standard_table_123"
    assert data["tables"]["standard"]["stripe_publishable_key"] == "pk_standard_key"
    assert "updated_at" in data["tables"]["standard"]

    assert "always_free" in data["tables"]
    assert data["tables"]["always_free"]["pricing_table_id"] == "always_free_table_123"
    assert data["tables"]["always_free"]["stripe_publishable_key"] == "pk_always_free_key"
    assert "updated_at" in data["tables"]["always_free"]

def test_get_all_pricing_tables_partial(client, db, admin_token):
    """Test that a system admin can get all pricing tables when only some exist"""
    # Create only standard pricing table
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_standard_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.commit()

    # Get all pricing tables
    response = client.get("/pricing-tables/list", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    assert "tables" in data
    assert "standard" in data["tables"]
    assert data["tables"]["standard"]["pricing_table_id"] == "standard_table_123"
    assert data["tables"]["standard"]["stripe_publishable_key"] == "pk_standard_key"

    assert "always_free" not in data["tables"]

def test_get_pricing_table_defaults_to_standard(client, db, team_admin_token):
    """Test that get pricing table defaults to standard for non-always-free teams"""
    # Create standard pricing table
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.commit()

    # Get pricing table without specifying table_type
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "standard_table_123"
    assert data["stripe_publishable_key"] == "pk_test_string"
    assert "updated_at" in data

def test_get_pricing_table_defaults_to_always_free(client, db, team_admin_token, test_team):
    """Test that get pricing table defaults to always_free for always-free teams"""
    # Set team as always-free
    test_team.is_always_free = True
    db.add(test_team)
    db.commit()

    # Create always-free pricing table
    always_free_table = DBPricingTable(
        table_type="always_free",
        pricing_table_id="always_free_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(always_free_table)
    db.commit()

    # Get pricing table without specifying table_type
    response = client.get("/pricing-tables", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "always_free_table_123"
    assert data["stripe_publishable_key"] == "pk_test_string"
    assert "updated_at" in data

def test_get_pricing_table_with_explicit_type(client, db, team_admin_token):
    """Test that get pricing table works with explicit table_type parameter"""
    # Create both pricing tables
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    always_free_table = DBPricingTable(
        table_type="always_free",
        pricing_table_id="always_free_table_123",
        stripe_publishable_key="pk_test_string",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.add(always_free_table)
    db.commit()

    # Get standard table explicitly
    response = client.get("/pricing-tables?table_type=standard", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "standard_table_123"

    # Get always_free table explicitly
    response = client.get("/pricing-tables?table_type=always_free", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()
    assert data["pricing_table_id"] == "always_free_table_123"

def test_get_pricing_table_invalid_type(client, db, team_admin_token):
    """Test that get pricing table returns error for invalid table type"""
    response = client.get("/pricing-tables?table_type=invalid_type", headers={"Authorization": f"Bearer {team_admin_token}"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid table type" in response.json()["detail"]

def test_delete_pricing_table_invalid_type(client, db, admin_token):
    """Test that delete pricing table returns error for invalid table type"""
    response = client.delete("/pricing-tables?table_type=invalid_type", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == status.HTTP_400_BAD_REQUEST
    assert "Invalid table type" in response.json()["detail"]

def test_get_all_pricing_tables_team_admin_forbidden(client, db, test_team_admin):
    """Test that a team admin cannot get all pricing tables"""
    # Login as team admin
    response = client.post(
        "/auth/login",
        data={"username": test_team_admin.email, "password": "password123"}
    )
    assert response.status_code == status.HTTP_200_OK
    token = response.json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}

    # Try to get all pricing tables
    response = client.get("/pricing-tables/list", headers=headers)
    assert response.status_code == status.HTTP_403_FORBIDDEN

def test_get_all_pricing_tables_with_gpt(client, db, admin_token):
    """Test that get all pricing tables returns all table types including gpt"""
    # Create pricing tables for all types
    standard_table = DBPricingTable(
        table_type="standard",
        pricing_table_id="standard_table_123",
        stripe_publishable_key="pk_standard_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    always_free_table = DBPricingTable(
        table_type="always_free",
        pricing_table_id="always_free_table_123",
        stripe_publishable_key="pk_always_free_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    gpt_table = DBPricingTable(
        table_type="gpt",
        pricing_table_id="gpt_table_123",
        stripe_publishable_key="pk_gpt_key",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(standard_table)
    db.add(always_free_table)
    db.add(gpt_table)
    db.commit()

    # Get all pricing tables
    response = client.get("/pricing-tables/list", headers={"Authorization": f"Bearer {admin_token}"})
    assert response.status_code == status.HTTP_200_OK
    data = response.json()

    # Check that all table types are returned
    assert "tables" in data
    assert "standard" in data["tables"]
    assert "always_free" in data["tables"]
    assert "gpt" in data["tables"]

    # Verify table contents
    assert data["tables"]["standard"]["pricing_table_id"] == "standard_table_123"
    assert data["tables"]["standard"]["stripe_publishable_key"] == "pk_standard_key"
    assert "updated_at" in data["tables"]["standard"]

    assert data["tables"]["always_free"]["pricing_table_id"] == "always_free_table_123"
    assert data["tables"]["always_free"]["stripe_publishable_key"] == "pk_always_free_key"
    assert "updated_at" in data["tables"]["always_free"]

    assert data["tables"]["gpt"]["pricing_table_id"] == "gpt_table_123"
    assert data["tables"]["gpt"]["stripe_publishable_key"] == "pk_gpt_key"
    assert "updated_at" in data["tables"]["gpt"]