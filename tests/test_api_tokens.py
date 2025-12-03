from app.db.models import DBAPIToken

def test_create_api_token(client, test_token, test_user):
    """Test creating a new API token"""
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"name": "Test Token"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Token"
    assert "token" in data
    assert "id" in data
    assert "created_at" in data
    assert data["last_used_at"] is None

def test_list_api_tokens(client, test_token, test_user, db):
    """Test listing API tokens"""
    # Create a test token in the database
    test_api_token = DBAPIToken(
        name="Test Token",
        token="test-token-123",
        user_id=test_user.id
    )
    db.add(test_api_token)
    db.commit()

    response = client.get(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert isinstance(data, list)
    assert len(data) > 0
    assert data[0]["name"] == "Test Token"
    assert data[0]["user_id"] == test_user.id
    assert "created_at" in data[0]
    assert "last_used_at" in data[0]
    # Token should not be included in response for security
    assert "token" not in data[0]

def test_delete_api_token(client, test_token, test_user, db):
    """Test deleting an API token"""
    # Create a test token in the database
    test_api_token = DBAPIToken(
        name="Test Token",
        token="test-token-123",
        user_id=test_user.id
    )
    db.add(test_api_token)
    db.commit()

    response = client.delete(
        f"/auth/token/{test_api_token.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 200
    assert response.json()["message"] == "Token deleted successfully"

    # Verify token is actually deleted
    db_token = db.query(DBAPIToken).filter(DBAPIToken.id == test_api_token.id).first()
    assert db_token is None

def test_delete_nonexistent_token(client, test_token):
    """Test deleting a nonexistent API token"""
    response = client.delete(
        "/auth/token/99999",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 404
    assert "Token not found" in response.json()["detail"]

def test_delete_other_users_token(client, test_token, test_admin, db):
    """Test that a user cannot delete another user's token"""
    # Create a token owned by the admin
    admin_token = DBAPIToken(
        name="Admin Token",
        token="admin-token-123",
        user_id=test_admin.id
    )
    db.add(admin_token)
    db.commit()

    # Try to delete admin's token as regular user
    response = client.delete(
        f"/auth/token/{admin_token.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 404
    assert "Token not found" in response.json()["detail"]

def test_create_api_token_unauthenticated(client):
    """Test that unauthenticated users cannot create tokens"""
    response = client.post(
        "/auth/token",
        json={"name": "Test Token"}
    )

    assert response.status_code == 401
    assert "Could not validate credentials" in response.json()["detail"]

def test_list_api_tokens_unauthenticated(client):
    """Test that unauthenticated users cannot list tokens"""
    response = client.get("/auth/token")

    assert response.status_code == 401
    assert "Could not validate credentials" in response.json()["detail"]