import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBUser
from app.db.database import get_db
from app.main import app
from app.api.auth import get_current_user
from app.core.security import get_password_hash
from datetime import datetime, UTC

client = TestClient(app)

def test_register_team(client):
    """Test registering a new team"""
    response = client.post(
        "/teams/",
        json={
            "name": "Test Team",
            "email": "team@example.com",
            "phone": "1234567890",
            "billing_address": "123 Test St, Test City, 12345"
        }
    )
    assert response.status_code == 201
    team_data = response.json()
    assert team_data["name"] == "Test Team"
    assert team_data["email"] == "team@example.com"
    assert team_data["phone"] == "1234567890"
    assert team_data["billing_address"] == "123 Test St, Test City, 12345"
    assert team_data["is_active"] is True
    assert "id" in team_data
    assert "created_at" in team_data
    assert "updated_at" in team_data

def test_register_team_duplicate_email(client, db):
    """Test registering a team with an email that already exists"""
    # First, create a team
    team = DBTeam(
        name="Existing Team",
        email="existing@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Try to register a new team with the same email
    response = client.post(
        "/teams/",
        json={
            "name": "New Team",
            "email": "existing@example.com",
            "phone": "0987654321",
            "billing_address": "456 New St, New City, 54321"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

def test_list_teams(client, admin_token, db):
    """Test listing all teams (admin only)"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()

    # List teams as admin
    response = client.get(
        "/teams/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    teams = response.json()
    assert isinstance(teams, list)
    assert len(teams) >= 1
    assert any(t["email"] == "testteam@example.com" for t in teams)

def test_list_teams_unauthorized(client, test_token):
    """Test listing teams without admin privileges"""
    response = client.get(
        "/teams/",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

def test_get_team(client, admin_token, db):
    """Test getting a team by ID"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Get team as admin
    response = client.get(
        f"/teams/{team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["name"] == "Test Team"
    assert team_data["email"] == "testteam@example.com"
    assert team_data["id"] == team.id
    assert "users" in team_data
    assert isinstance(team_data["users"], list)

def test_get_team_as_team_user(client, db):
    """Test getting a team by ID as a user associated with that team"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    team_id = team.id  # Store the ID for later use

    # Create a user associated with the team
    user = DBUser(
        email="teamuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="admin",
        team_id=team_id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()

    # Login as the team user
    login_response = client.post(
        "/auth/login",
        data={"username": "teamuser@example.com", "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Get team as the associated user
    response = client.get(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["name"] == "Test Team"
    assert team_data["email"] == "testteam@example.com"
    assert team_data["id"] == team_id

def test_get_team_unauthorized(client, test_token, db):
    """Test getting a team by ID as a user not associated with that team"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()

    # Try to get the team as a user not associated with it
    response = client.get(
        f"/teams/{team.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to access this team"

def test_update_team(client, admin_token, db):
    """Test updating a team as an admin"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Update the team
    response = client.put(
        f"/teams/{team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Updated Team",
            "phone": "0987654321",
            "billing_address": "456 Updated St, Updated City, 54321"
        }
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["name"] == "Updated Team"
    assert team_data["email"] == "testteam@example.com"  # Email shouldn't change
    assert team_data["phone"] == "0987654321"
    assert team_data["billing_address"] == "456 Updated St, Updated City, 54321"

def test_update_team_as_team_admin(client, db):
    """Test updating a team as a team admin"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    team_id = team.id  # Store the ID for later use

    # Create a user associated with the team with admin role
    user = DBUser(
        email="teamadmin@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="admin",
        team_id=team_id,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()

    # Login as the team admin
    login_response = client.post(
        "/auth/login",
        data={"username": "teamadmin@example.com", "password": "password123"}
    )
    assert login_response.status_code == 200
    token = login_response.json()["access_token"]

    # Update the team
    response = client.put(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "name": "Updated Team",
            "phone": "0987654321",
            "billing_address": "456 Updated St, Updated City, 54321"
        }
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["name"] == "Updated Team"
    assert team_data["email"] == "testteam@example.com"  # Email shouldn't change
    assert team_data["phone"] == "0987654321"
    assert team_data["billing_address"] == "456 Updated St, Updated City, 54321"

def test_update_team_unauthorized(client, test_token, db):
    """Test updating a team as a user not associated with that team"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()

    # Try to update the team as a user not associated with it
    response = client.put(
        f"/teams/{team.id}",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "Updated Team",
            "phone": "0987654321",
            "billing_address": "456 Updated St, Updated City, 54321"
        }
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action for this team"

def test_delete_team(client, admin_token, db):
    """Test deleting a team as an admin"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    team_id = team.id  # Store the ID for later use

    # Delete the team
    response = client.delete(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Team deleted successfully"

    # Verify the team is deleted
    db_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    assert db_team is None

def test_delete_team_unauthorized(client, test_token, db):
    """Test deleting a team as a non-admin user"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        email="testteam@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()

    # Try to delete the team as a non-admin user
    response = client.delete(
        f"/teams/{team.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"