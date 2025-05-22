import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db.models import DBProduct, DBUser, DBTeam, DBTeamProduct
from datetime import datetime, UTC

def test_create_product_as_system_admin(client, admin_token, db):
    """
    Test that a system admin can create a product.

    GIVEN: The authenticated user is a system admin
    WHEN: They create a product
    THEN: A 201 - Created is returned with the product data
    """
    response = client.post(
        "/products/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "id": "prod_test123",
            "name": "Test Product",
            "user_count": 5,
            "keys_per_user": 2,
            "total_key_count": 10,
            "service_key_count": 2,
            "max_budget_per_key": 50.0,
            "rpm_per_key": 1000,
            "vector_db_count": 1,
            "vector_db_storage": 100,
            "renewal_period_days": 30,
            "active": True
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Product"
    assert data["id"] == "prod_test123"
    assert data["user_count"] == 5
    assert data["keys_per_user"] == 2
    assert data["total_key_count"] == 10
    assert data["service_key_count"] == 2
    assert data["max_budget_per_key"] == 50.0
    assert data["rpm_per_key"] == 1000
    assert data["vector_db_count"] == 1
    assert data["vector_db_storage"] == 100
    assert data["renewal_period_days"] == 30
    assert data["active"] is True
    assert "created_at" in data

def test_create_product_duplicate_id(client, admin_token, db):
    """
    Test that creating a product with a duplicate ID fails.

    GIVEN: A product with a specific ID exists
    WHEN: A system admin tries to create another product with the same ID
    THEN: A 400 - Bad Request is returned
    """
    # First create a product
    client.post(
        "/products/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "id": "prod_test123",
            "name": "Test Product",
            "user_count": 5,
            "keys_per_user": 2,
            "total_key_count": 10,
            "service_key_count": 2,
            "max_budget_per_key": 50.0,
            "rpm_per_key": 1000,
            "vector_db_count": 1,
            "vector_db_storage": 100,
            "renewal_period_days": 30,
            "active": True
        }
    )

    # Try to create another product with the same ID
    response = client.post(
        "/products/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "id": "prod_test123",
            "name": "Another Product",
            "user_count": 5,
            "keys_per_user": 2,
            "total_key_count": 10,
            "service_key_count": 2,
            "max_budget_per_key": 50.0,
            "rpm_per_key": 1000,
            "vector_db_count": 1,
            "vector_db_storage": 100,
            "renewal_period_days": 30,
            "active": True
        }
    )
    assert response.status_code == 400
    assert "already exists" in response.json()["detail"]

def test_create_product_unauthorized(client, test_token, db):
    """
    Test that a non-admin user cannot create a product.

    GIVEN: The authenticated user is not a system admin
    WHEN: They try to create a product
    THEN: A 403 - Forbidden is returned
    """
    response = client.post(
        "/products/",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "id": "prod_test123",
            "name": "Test Product",
            "user_count": 5,
            "keys_per_user": 2,
            "total_key_count": 10,
            "service_key_count": 2,
            "max_budget_per_key": 50.0,
            "rpm_per_key": 1000,
            "vector_db_count": 1,
            "vector_db_storage": 100,
            "renewal_period_days": 30,
            "active": True
        }
    )
    assert response.status_code == 403

def test_list_products_as_team_admin(client, team_admin_token, db):
    """
    Test that a team admin can list products.

    GIVEN: The authenticated user is a team admin
    WHEN: They request the list of products
    THEN: A 200 - OK is returned with the list of products
    """
    # Create some test products
    db_product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
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
    db_product2 = DBProduct(
        id="prod_test2",
        name="Test Product 2",
        user_count=10,
        keys_per_user=3,
        total_key_count=30,
        service_key_count=5,
        max_budget_per_key=100.0,
        rpm_per_key=2000,
        vector_db_count=2,
        vector_db_storage=200,
        renewal_period_days=60,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(db_product1)
    db.add(db_product2)
    db.commit()

    response = client.get(
        "/products/",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) >= 2
    assert any(p["name"] == "Test Product 1" for p in data)
    assert any(p["name"] == "Test Product 2" for p in data)

def test_list_products_unauthorized(client, test_token, db):
    """
    Test that a regular user cannot list products.

    GIVEN: The authenticated user is not a team admin
    WHEN: They try to list products
    THEN: A 403 - Forbidden is returned
    """
    response = client.get(
        "/products/",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403

def test_get_product_as_team_admin(client, team_admin_token, db):
    """
    Test that a team admin can get a specific product.

    GIVEN: The authenticated user is a team admin
    WHEN: They request a specific product
    THEN: A 200 - OK is returned with the product data
    """
    # Create a test product
    db_product = DBProduct(
        id="prod_test123",
        name="Test Product",
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
    db.add(db_product)
    db.commit()

    response = client.get(
        f"/products/{db_product.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Product"
    assert data["id"] == "prod_test123"
    assert data["user_count"] == 5
    assert data["keys_per_user"] == 2
    assert data["total_key_count"] == 10
    assert data["service_key_count"] == 2
    assert data["max_budget_per_key"] == 50.0
    assert data["rpm_per_key"] == 1000
    assert data["vector_db_count"] == 1
    assert data["vector_db_storage"] == 100
    assert data["renewal_period_days"] == 30
    assert data["active"] is True

def test_get_product_not_found(client, team_admin_token, db):
    """
    Test that getting a non-existent product returns 404.

    GIVEN: The authenticated user is a team admin
    WHEN: They request a non-existent product
    THEN: A 404 - Not Found is returned
    """
    response = client.get(
        "/products/prod_nonexistent",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 404

def test_update_product_as_system_admin(client, admin_token, db):
    """
    Test that a system admin can update a product.

    GIVEN: The authenticated user is a system admin
    WHEN: They update a product
    THEN: A 200 - OK is returned with the updated product data
    """
    # Create a test product
    db_product = DBProduct(
        id="prod_test123",
        name="Test Product",
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
    db.add(db_product)
    db.commit()

    response = client.put(
        f"/products/{db_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Updated Product",
            "user_count": 10,
            "keys_per_user": 3,
            "total_key_count": 30,
            "service_key_count": 5,
            "max_budget_per_key": 100.0,
            "rpm_per_key": 2000,
            "vector_db_count": 2,
            "vector_db_storage": 200,
            "renewal_period_days": 60,
            "active": False
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Product"
    assert data["id"] == "prod_test123"  # ID should remain unchanged
    assert data["user_count"] == 10
    assert data["keys_per_user"] == 3
    assert data["total_key_count"] == 30
    assert data["service_key_count"] == 5
    assert data["max_budget_per_key"] == 100.0
    assert data["rpm_per_key"] == 2000
    assert data["vector_db_count"] == 2
    assert data["vector_db_storage"] == 200
    assert data["renewal_period_days"] == 60
    assert data["active"] is False

def test_update_product_unauthorized(client, team_admin_token, db):
    """
    Test that a team admin cannot update a product.

    GIVEN: The authenticated user is a team admin
    WHEN: They try to update a product
    THEN: A 403 - Forbidden is returned
    """
    # Create a test product
    db_product = DBProduct(
        id="prod_test123",
        name="Test Product",
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
    db.add(db_product)
    db.commit()

    response = client.put(
        f"/products/{db_product.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "name": "Updated Product"
        }
    )
    assert response.status_code == 403

def test_delete_product_as_system_admin(client, admin_token, db):
    """
    Test that a system admin can delete a product.

    GIVEN: The authenticated user is a system admin
    WHEN: They delete a product
    THEN: A 200 - OK is returned and the product is deleted
    """
    # Create a test product
    db_product = DBProduct(
        id="prod_test123",
        name="Test Product",
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
    db.add(db_product)
    db.commit()

    response = client.delete(
        f"/products/{db_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200

    # Verify the product is deleted
    deleted_product = db.query(DBProduct).filter(DBProduct.id == db_product.id).first()
    assert deleted_product is None

def test_delete_product_with_team_association(client, admin_token, db, test_team, test_product):
    """
    Test that a product cannot be deleted if it's associated with a team.

    GIVEN: A product which has been applied to a team
    WHEN: An authorised user tries to delete the product
    THEN: An error is returned
    """
    # Associate the product with a team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Try to delete the product
    response = client.delete(
        f"/products/{test_product.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 400
    assert "cannot delete product" in response.json()["detail"].lower()

    # Verify the product still exists
    existing_product = db.query(DBProduct).filter(DBProduct.id == test_product.id).first()
    assert existing_product is not None