from fastapi.testclient import TestClient
from app.db.models import DBTeam, DBUser
from app.main import app
from app.core.security import get_password_hash
from datetime import datetime, UTC

client = TestClient(app)

def test_register_team(client):
    """Test registering a new team"""
    response = client.post(
        "/teams/",
        json={
            "name": "Test Team",
            "admin_email": "team@example.com",
            "phone": "1234567890",
            "billing_address": "123 Test St, Test City, 12345"
        }
    )
    assert response.status_code == 201
    team_data = response.json()
    assert team_data["name"] == "Test Team"
    assert team_data["admin_email"] == "team@example.com"
    assert team_data["phone"] == "1234567890"
    assert team_data["billing_address"] == "123 Test St, Test City, 12345"
    assert team_data["is_active"] is True
    assert "id" in team_data
    assert "created_at" in team_data
    assert "updated_at" in team_data

def test_register_team_duplicate_admin_email(client, db):
    """Test registering a team with an email that already exists"""
    # First, create a team
    team = DBTeam(
        name="Existing Team",
        admin_email="existing@example.com",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Try to register a new team with the same admin_email
    response = client.post(
        "/teams/",
        json={
            "name": "New Team",
            "admin_email": "existing@example.com",
            "phone": "0987654321",
            "billing_address": "456 New St, New City, 54321"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

def test_list_teams(client, admin_token, db, test_team):
    """Test listing all teams (admin only)"""
    # List teams as admin
    response = client.get(
        "/teams/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    teams = response.json()
    assert isinstance(teams, list)
    assert len(teams) >= 1
    assert any(t["admin_email"] == "testteam@example.com" for t in teams)

def test_list_teams_unauthorized(client, test_token):
    """Test listing teams without admin privileges"""
    response = client.get(
        "/teams/",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

def test_get_team(client, admin_token, test_team):
    """Test getting a team by ID"""
    # Get team as admin
    response = client.get(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["name"] == "Test Team"
    assert team_data["admin_email"] == "testteam@example.com"
    assert team_data["id"] == test_team.id
    assert "users" in team_data
    assert isinstance(team_data["users"], list)

def test_get_team_as_team_user(client, db):
    """Test getting a team by ID as a user associated with that team"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        admin_email="testteam@example.com",
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
    assert team_data["admin_email"] == "testteam@example.com"
    assert team_data["id"] == team_id

def test_get_team_unauthorized(client, test_token, test_team):
    """Test getting a team by ID as a user not associated with that team"""
    # Try to get the team as a user not associated with it
    response = client.get(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

def test_update_team(client, admin_token, test_team):
    """Test updating a team as an admin"""
    # Update the team
    response = client.put(
        f"/teams/{test_team.id}",
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
    assert team_data["admin_email"] == "testteam@example.com"  # admin_email shouldn't change
    assert team_data["phone"] == "0987654321"
    assert team_data["billing_address"] == "456 Updated St, Updated City, 54321"

def test_update_team_as_team_admin(client, db):
    """Test updating a team as a team admin"""
    # Create a test team
    team = DBTeam(
        name="Test Team",
        admin_email="testteam@example.com",
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
    assert team_data["admin_email"] == "testteam@example.com"  # admin_email shouldn't change
    assert team_data["phone"] == "0987654321"
    assert team_data["billing_address"] == "456 Updated St, Updated City, 54321"

def test_update_team_unauthorized(client, test_token, test_team):
    """Test updating a team as a user not associated with that team"""
    # Try to update the team as a user not associated with it
    response = client.put(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "Updated Team",
            "phone": "0987654321",
            "billing_address": "456 Updated St, Updated City, 54321"
        }
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

def test_delete_team(client, admin_token, test_team, db):
    """Test deleting a team as an admin"""
    # Delete the team
    response = client.delete(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Team deleted successfully"

    # Verify the team is deleted
    db_team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    assert db_team is None

def test_delete_team_unauthorized(client, test_token, test_team):
    """Test deleting a team as a non-admin user"""
    # Try to delete the team as a non-admin user
    response = client.delete(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

def test_add_user_to_second_team(client, admin_token, db, test_team, test_team_user):
    """Test that a user cannot be added to a second team when already a member of another team"""
    # Create a second team
    team2 = DBTeam(
        name="Team 2",
        admin_email="team2@example.com",
        phone="0987654321",
        billing_address="456 Team 2 St, City 2, 54321",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team2)
    db.commit()
    db.refresh(team2)

    # Try to add the user to Team 2
    response = client.post(
        f"/users/{test_team_user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": team2.id}
    )
    assert response.status_code == 400
    assert "User is already a member of another team" in response.json()["detail"]

def test_make_team_user_admin(client, admin_token, test_team_user):
    """Test that a user who is a member of a team cannot be made an admin"""
    # Try to make the user an admin
    response = client.put(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_admin": True}
    )
    assert response.status_code == 400
    assert "Team members cannot be made administrators" in response.json()["detail"]

def test_add_non_team_user_to_team(client, admin_token, db, test_team):
    """Test that a non-admin user who is not a member of any team can be successfully added to a team"""
    # Create a user who is not a member of any team
    user = DBUser(
        email="nonteamuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="user",
        team_id=None,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Ensure team is attached to session and get its ID
    db.add(test_team)
    db.refresh(test_team)
    team_id = test_team.id  # Store the ID for later use

    # Add the user to the team
    response = client.post(
        f"/users/{user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": team_id}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["team_id"] == team_id
    assert user_data["is_admin"] is False

    # Verify the user is actually in the team in the database
    db_user = db.query(DBUser).filter(DBUser.id == user.id).first()
    assert db_user.team_id == team_id

    # Verify the user appears in the team's user list
    team_response = client.get(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert team_response.status_code == 200
    team_data = team_response.json()
    assert any(u["id"] == user.id for u in team_data["users"])

def test_add_admin_user_to_team(client, admin_token, db, test_team):
    """Test that an admin user cannot be added to a team"""
    # Create an admin user who is not a member of any team
    user = DBUser(
        email="adminuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=True,
        role="user",
        team_id=None,
        created_at=datetime.now(UTC)
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    # Try to add the admin user to the team
    response = client.post(
        f"/users/{user.id}/add-to-team",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"team_id": test_team.id}
    )
    assert response.status_code == 400
    assert "Administrators cannot be added to teams" in response.json()["detail"]

def test_remove_user_from_team(client, admin_token, test_team_user):
    """Test removing a user from a team"""
    response = client.post(
        f"/users/{test_team_user.id}/remove-from-team",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["team_id"] is None

def test_remove_user_not_in_team(client, admin_token, test_user):
    """Test removing a user who is not in a team"""
    response = client.post(
        f"/users/{test_user.id}/remove-from-team",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 400
    assert "User is not a member of any team" in response.json()["detail"]

def test_team_admin_cannot_remove_user_from_team(client, team_admin_token, test_team_user):
    """
    Test that a team admin cannot remove a user from their team.

    GIVEN: User A is a member of Team 1
    WHEN: a team admin tries to remove the user from the team
    THEN: A 403 - Forbidden is returned
    """
    response = client.post(
        f"/users/{test_team_user.id}/remove-from-team",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

    # Verify user is still in the team
    response = client.get(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["team_id"] == test_team_user.team_id