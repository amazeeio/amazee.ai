import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db.models import DBProduct, DBUser, DBTeam
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
            "name": "Test Product",
            "stripe_lookup_key": "test_product_123",
            "active": True
        }
    )
    assert response.status_code == 201
    data = response.json()
    assert data["name"] == "Test Product"
    assert data["stripe_lookup_key"] == "test_product_123"
    assert data["active"] is True
    assert "id" in data
    assert "created_at" in data

def test_create_product_duplicate_key(client, admin_token, db):
    """
    Test that creating a product with a duplicate stripe_lookup_key fails.

    GIVEN: A product with a specific stripe_lookup_key exists
    WHEN: A system admin tries to create another product with the same key
    THEN: A 400 - Bad Request is returned
    """
    # First create a product
    client.post(
        "/products/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Test Product",
            "stripe_lookup_key": "test_product_123",
            "active": True
        }
    )

    # Try to create another product with the same key
    response = client.post(
        "/products/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Another Product",
            "stripe_lookup_key": "test_product_123",
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
            "name": "Test Product",
            "stripe_lookup_key": "test_product_123",
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
        name="Test Product 1",
        stripe_lookup_key="test_product_1",
        active=True,
        created_at=datetime.now(UTC)
    )
    db_product2 = DBProduct(
        name="Test Product 2",
        stripe_lookup_key="test_product_2",
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
        name="Test Product",
        stripe_lookup_key="test_product_123",
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
    assert data["stripe_lookup_key"] == "test_product_123"
    assert data["active"] is True

def test_get_product_not_found(client, team_admin_token, db):
    """
    Test that getting a non-existent product returns 404.

    GIVEN: The authenticated user is a team admin
    WHEN: They request a non-existent product
    THEN: A 404 - Not Found is returned
    """
    response = client.get(
        "/products/99999",
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
        name="Test Product",
        stripe_lookup_key="test_product_123",
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
            "active": False
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Updated Product"
    assert data["stripe_lookup_key"] == "test_product_123"  # Unchanged
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
        name="Test Product",
        stripe_lookup_key="test_product_123",
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
        name="Test Product",
        stripe_lookup_key="test_product_123",
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
    assert response.json()["message"] == "Product deleted successfully"

    # Verify the product is deleted
    deleted_product = db.query(DBProduct).filter(DBProduct.id == db_product.id).first()
    assert deleted_product is None

def test_delete_product_unauthorized(client, team_admin_token, db):
    """
    Test that a team admin cannot delete a product.

    GIVEN: The authenticated user is a team admin
    WHEN: They try to delete a product
    THEN: A 403 - Forbidden is returned
    """
    # Create a test product
    db_product = DBProduct(
        name="Test Product",
        stripe_lookup_key="test_product_123",
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(db_product)
    db.commit()

    response = client.delete(
        f"/products/{db_product.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 403

    # Verify the product still exists
    product = db.query(DBProduct).filter(DBProduct.id == db_product.id).first()
    assert product is not None