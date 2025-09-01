from fastapi.testclient import TestClient
from app.db.models import DBTeam, DBUser, DBPrivateAIKey, DBProduct, DBTeamProduct
from app.main import app
from app.core.security import get_password_hash
from datetime import datetime, UTC
from unittest.mock import patch, MagicMock

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

def test_register_team_duplicate_admin_email_case_insensitive(client, db):
    """
    Given a team with admin_email "existing@example.com" exists
    When registering a new team with admin_email "EXISTING@EXAMPLE.COM"
    Then the registration should fail with "Email already registered" error
    """
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

    # Try to register a new team with the same admin_email but different case
    response = client.post(
        "/teams/",
        json={
            "name": "New Team",
            "admin_email": "EXISTING@EXAMPLE.COM",
            "phone": "0987654321",
            "billing_address": "456 New St, New City, 54321"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

def test_register_team_duplicate_admin_email_case_insensitive_reverse(client, db):
    """
    Given a team with admin_email "EXISTING@EXAMPLE.COM" exists
    When registering a new team with admin_email "existing@example.com"
    Then the registration should fail with "Email already registered" error
    """
    # First, create a team with uppercase email
    team = DBTeam(
        name="Existing Team",
        admin_email="EXISTING@EXAMPLE.COM",
        phone="1234567890",
        billing_address="123 Test St, Test City, 12345",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add(team)
    db.commit()
    db.refresh(team)

    # Try to register a new team with the same admin_email but lowercase
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

def test_delete_team_with_products(client, admin_token, db, test_team, test_product):
    """Test deleting a team that has associated products"""
    product_id = test_product.id
    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product_id
    )
    db.add(team_product)
    db.commit()

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

    # Verify the product association is removed
    db_team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == product_id
    ).first()
    assert db_team_product is None

    # Verify the product still exists (should not be deleted)
    db_product = db.query(DBProduct).filter(DBProduct.id == product_id).first()
    assert db_product is not None

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

@patch("app.services.litellm.requests.post")
@patch("app.api.teams.SESService")
def test_extend_team_trial_success(mock_ses_class, mock_litellm_post, client, admin_token, test_team, test_region, db):
    """Test successfully extending a team's trial period"""
    # Mock SES service
    mock_ses_instance = MagicMock()
    mock_ses_class.return_value = mock_ses_instance

    # Ensure team is attached to session
    db.add(test_team)
    db.commit()

    # Mock LiteLLM API response
    mock_litellm_post.return_value.status_code = 200
    mock_litellm_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_litellm_post.return_value.raise_for_status.return_value = None

    # Create a test key for the team
    test_key = DBPrivateAIKey(
        name="Test Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test-private-key-123",
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Extend the team's trial
    response = client.post(
        f"/teams/{test_team.id}/extend-trial",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Team trial extended successfully"

    # Verify the team's last payment was updated
    updated_team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    assert updated_team.last_payment is not None
    assert (datetime.now(UTC) - updated_team.last_payment).total_seconds() < 5  # Within last 5 seconds

    # Verify LiteLLM API was called to update key restrictions
    mock_litellm_post.assert_called_with(
        f"{test_region.litellm_api_url}/key/update",
        headers={"Authorization": f"Bearer {test_region.litellm_api_key}"},
        json={
            "key": test_key.litellm_token,
            "duration": "30d",
            "budget_duration": "30d",
            "max_budget": 27.0,
            "rpm_limit": 500
        }
    )

    # Verify email was sent
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args["to_addresses"] == [test_team.admin_email]
    assert call_args["template_name"] == "trial-extended"
    assert call_args["template_data"]["name"] == test_team.name

@patch("app.services.litellm.requests.post")
@patch("app.api.teams.SESService")
def test_extend_team_trial_litellm_error(mock_ses_class, mock_litellm_post, client, admin_token, test_team, test_region, db):
    """Test extending a team's trial when LiteLLM API fails"""
    # Mock SES service
    mock_ses_instance = MagicMock()
    mock_ses_class.return_value = mock_ses_instance

    # Ensure team is attached to session
    db.add(test_team)
    db.commit()

    # Mock LiteLLM API error
    mock_litellm_post.return_value.status_code = 500
    mock_litellm_post.return_value.raise_for_status.side_effect = Exception("API Error")

    # Create a test key for the team
    test_key = DBPrivateAIKey(
        name="Test Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test-private-key-123",
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Extend the team's trial
    response = client.post(
        f"/teams/{test_team.id}/extend-trial",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200  # Should still succeed despite LiteLLM error
    assert response.json()["message"] == "Team trial extended successfully"

    # Verify the team's last payment was updated
    updated_team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    assert updated_team.last_payment is not None

    # Verify email was still sent
    mock_ses_instance.send_email.assert_called_once()

@patch("app.services.litellm.requests.post")
@patch("app.api.teams.SESService")
def test_extend_team_trial_email_error(mock_ses_class, mock_litellm_post, client, admin_token, test_team, test_region, db):
    """Test extending a team's trial when email sending fails"""
    # Mock SES service with error
    mock_ses_instance = MagicMock()
    mock_ses_instance.send_email.side_effect = Exception("Email Error")
    mock_ses_class.return_value = mock_ses_instance

    # Ensure team is attached to session
    db.add(test_team)
    db.commit()

    # Mock LiteLLM API success
    mock_litellm_post.return_value.status_code = 200
    mock_litellm_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_litellm_post.return_value.raise_for_status.return_value = None

    # Create a test key for the team
    test_key = DBPrivateAIKey(
        name="Test Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test-private-key-123",
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Extend the team's trial
    response = client.post(
        f"/teams/{test_team.id}/extend-trial",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200  # Should still succeed despite email error
    assert response.json()["message"] == "Team trial extended successfully"

    # Verify the team's last payment was updated
    updated_team = db.query(DBTeam).filter(DBTeam.id == test_team.id).first()
    assert updated_team.last_payment is not None

    # Verify LiteLLM API was called
    mock_litellm_post.assert_called()

@patch("app.api.teams.SESService")
def test_toggle_always_free_as_admin(mock_ses, client, admin_token, test_team, test_team_admin, db):
    """Test toggling always-free status as an admin"""
    # Mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email.return_value = True

    # Toggle always-free on
    response = client.put(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_always_free": True}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["is_always_free"] is True

    # Verify email was sent
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args["to_addresses"] == [test_team_admin.email]
    assert call_args["template_name"] == "always-free"
    assert call_args["template_data"]["name"] == test_team.name
    assert "dashboard_url" in call_args["template_data"]

    # Toggle always-free off
    response = client.put(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_always_free": False}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["is_always_free"] is False

    # Verify no additional email was sent
    assert mock_ses_instance.send_email.call_count == 1

def test_toggle_always_free_as_team_admin(client, team_admin_token, test_team):
    """Test that team admins cannot toggle always-free status"""
    response = client.put(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"is_always_free": True}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Only system administrators can toggle always-free status"

def test_toggle_always_free_as_team_user(client, test_token, test_team):
    """Test that regular team users cannot toggle always-free status"""
    response = client.put(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {test_token}"},
        json={"is_always_free": True}
    )
    assert response.status_code == 403
    assert response.json()["detail"] == "Not authorized to perform this action"

@patch("app.api.teams.SESService")
def test_toggle_always_free_email_error(mock_ses, client, admin_token, test_team, test_team_admin):
    """Test that team update succeeds even if email sending fails"""
    # Mock SES service with error
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email.side_effect = Exception("Email Error")

    # Toggle always-free on
    response = client.put(
        f"/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"is_always_free": True}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["is_always_free"] is True

    # Verify email was attempted
    mock_ses_instance.send_email.assert_called_once()

# Tests for team merge functionality
@patch("app.services.litellm.requests.post")
def test_merge_teams_endpoint_success(mock_post, client, admin_token, db):
    """Given a system admin and two teams
    When merging source team into target team
    Then the merge should succeed"""

    # Create source and target teams
    source_team = DBTeam(
        name="Source Team",
        admin_email="source@example.com",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    target_team = DBTeam(
        name="Target Team",
        admin_email="target@example.com",
        is_active=True,
        created_at=datetime.now(UTC)
    )
    db.add_all([source_team, target_team])
    db.commit()

    # Mock LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        f"/teams/{target_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": source_team.id,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "successfully merged team" in data["message"].lower()
    assert data["keys_migrated"] == 0
    assert data["users_migrated"] == 0

def test_merge_teams_endpoint_unauthorized(client, test_token, db):
    """Given a non-admin user
    When attempting to merge teams
    Then access should be denied"""

    response = client.post(
        "/teams/1/merge",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "source_team_id": 2,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_merge_teams_endpoint_invalid_teams(client, admin_token, db):
    """Given invalid team IDs
    When attempting to merge teams
    Then appropriate errors should be returned"""

    response = client.post(
        "/teams/999/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": 888,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 404
    assert "Target team not found" in response.json()["detail"]

def test_merge_teams_endpoint_same_team(client, admin_token, db, test_team):
    """Given the same team as source and target
    When attempting to merge teams
    Then an error should be returned"""

    response = client.post(
        f"/teams/{test_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": test_team.id,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 400
    assert "cannot merge a team into itself" in response.json()["detail"].lower()

@patch("app.services.litellm.requests.post")
@patch("app.services.litellm.requests.delete")
@patch("app.db.postgres.PostgresManager.delete_database")
@patch("app.api.private_ai_keys._get_key_if_allowed")
def test_merge_teams_endpoint_with_conflicts_delete_strategy(mock_get_key, mock_delete_db, mock_delete, mock_post, client, admin_token, db, test_region):
    """Given teams with conflicting key names and delete strategy
    When merging teams
    Then conflicting keys should be deleted from source team"""

    # Create teams with conflicting keys
    source_team = DBTeam(name="Source", admin_email="source@example.com", is_active=True)
    target_team = DBTeam(name="Target", admin_email="target@example.com", is_active=True)
    db.add_all([source_team, target_team])
    db.commit()

    # Create conflicting keys
    source_key = DBPrivateAIKey(
        name="conflict-key",
        team_id=source_team.id,
        region_id=test_region.id,
        litellm_token="source-token"
    )
    target_key = DBPrivateAIKey(
        name="conflict-key",
        team_id=target_team.id,
        region_id=test_region.id,
        litellm_token="target-token"
    )
    db.add_all([source_key, target_key])
    db.commit()

    # Mock _get_key_if_allowed to return the source key
    mock_get_key.return_value = source_key

    # Mock LiteLLM API responses
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None
    mock_delete.return_value.status_code = 200
    mock_delete.return_value.raise_for_status.return_value = None

    # Mock PostgresManager delete_database
    mock_delete_db.return_value = None

    response = client.post(
        f"/teams/{target_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": source_team.id,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "conflicts_resolved" in data
    assert len(data["conflicts_resolved"]) > 0
    assert "conflict-key" in data["conflicts_resolved"]

@patch("app.services.litellm.requests.post")
def test_merge_teams_endpoint_with_conflicts_rename_strategy(mock_post, client, admin_token, db, test_region):
    """Given teams with conflicting key names and rename strategy
    When merging teams
    Then conflicting keys should be renamed in source team"""

    # Create teams with conflicting keys
    source_team = DBTeam(name="Source", admin_email="source@example.com", is_active=True)
    target_team = DBTeam(name="Target", admin_email="target@example.com", is_active=True)
    db.add_all([source_team, target_team])
    db.commit()

    # Create conflicting keys
    source_key = DBPrivateAIKey(
        name="conflict-key",
        team_id=source_team.id,
        region_id=test_region.id,
        litellm_token="source-token"
    )
    target_key = DBPrivateAIKey(
        name="conflict-key",
        team_id=target_team.id,
        region_id=test_region.id,
        litellm_token="target-token"
    )
    db.add_all([source_key, target_key])
    db.commit()

    # Store IDs for verification after merge
    source_key_id = source_key.id

    # Mock LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        f"/teams/{target_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": source_team.id,
            "conflict_resolution_strategy": "rename",
            "rename_suffix": "_merged"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert "conflicts_resolved" in data
    assert len(data["conflicts_resolved"]) > 0

    # Verify the source key was renamed
    updated_source_key = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == source_key_id).first()
    assert updated_source_key.name == "conflict-key_merged"

@patch("app.services.litellm.requests.post")
def test_merge_teams_endpoint_with_conflicts_cancel_strategy(mock_post, client, admin_token, db, test_region):
    """Given teams with conflicting key names and cancel strategy
    When merging teams
    Then the merge should be cancelled"""

    # Create teams with conflicting keys
    source_team = DBTeam(name="Source", admin_email="source@example.com", is_active=True)
    target_team = DBTeam(name="Target", admin_email="target@example.com", is_active=True)
    db.add_all([source_team, target_team])
    db.commit()

    # Create conflicting keys
    source_key = DBPrivateAIKey(
        name="conflict-key",
        team_id=source_team.id,
        region_id=test_region.id,
        litellm_token="source-token"
    )
    target_key = DBPrivateAIKey(
        name="conflict-key",
        team_id=target_team.id,
        region_id=test_region.id,
        litellm_token="target-token"
    )
    db.add_all([source_key, target_key])
    db.commit()

    response = client.post(
        f"/teams/{target_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": source_team.id,
            "conflict_resolution_strategy": "cancel"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is False
    assert "merge cancelled" in data["message"].lower()
    assert "conflicts_resolved" in data
    assert len(data["conflicts_resolved"]) > 0
    assert data["keys_migrated"] == 0
    assert data["users_migrated"] == 0

    # Verify both teams still exist
    source_team_exists = db.query(DBTeam).filter(DBTeam.id == source_team.id).first()
    target_team_exists = db.query(DBTeam).filter(DBTeam.id == target_team.id).first()
    assert source_team_exists is not None
    assert target_team_exists is not None

@patch("app.services.litellm.requests.post")
def test_merge_teams_with_users_and_keys(mock_post, client, admin_token, db, test_region):
    """Given teams with users and keys
    When merging teams
    Then users and keys should be migrated correctly"""

    # Create teams
    source_team = DBTeam(name="Source", admin_email="source@example.com", is_active=True)
    target_team = DBTeam(name="Target", admin_email="target@example.com", is_active=True)
    db.add_all([source_team, target_team])
    db.commit()

    # Create users in source team
    source_user1 = DBUser(
        email="user1@source.com",
        hashed_password="hashed",
        team_id=source_team.id,
        is_active=True
    )
    source_user2 = DBUser(
        email="user2@source.com",
        hashed_password="hashed",
        team_id=source_team.id,
        is_active=True
    )
    db.add_all([source_user1, source_user2])

    # Create keys in source team
    source_key1 = DBPrivateAIKey(
        name="source-key-1",
        team_id=source_team.id,
        region_id=test_region.id,
        litellm_token="source-token-1"
    )
    source_key2 = DBPrivateAIKey(
        name="source-key-2",
        team_id=source_team.id,
        region_id=test_region.id,
        litellm_token="source-token-2"
    )
    db.add_all([source_key1, source_key2])
    db.commit()

    # Store IDs for verification after merge
    source_user1_id = source_user1.id
    source_user2_id = source_user2.id
    source_key1_id = source_key1.id
    source_key2_id = source_key2.id
    target_team_id = target_team.id

    # Mock LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.raise_for_status.return_value = None

    response = client.post(
        f"/teams/{target_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": source_team.id,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["success"] is True
    assert data["users_migrated"] == 2
    assert data["keys_migrated"] == 2

    # Verify users were migrated
    updated_user1 = db.query(DBUser).filter(DBUser.id == source_user1_id).first()
    updated_user2 = db.query(DBUser).filter(DBUser.id == source_user2_id).first()
    assert updated_user1.team_id == target_team_id
    assert updated_user2.team_id == target_team_id

    # Verify keys were migrated
    updated_key1 = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == source_key1_id).first()
    updated_key2 = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.id == source_key2_id).first()
    assert updated_key1.team_id == target_team_id
    assert updated_key2.team_id == target_team_id

    # Verify source team was deleted
    source_team_exists = db.query(DBTeam).filter(DBTeam.id == source_team.id).first()
    assert source_team_exists is None

def test_merge_teams_with_product_associations_fails(client, admin_token, db, test_product):
    """Given a source team with product associations
    When attempting to merge teams
    Then the merge should fail with a 400 error"""

    # Store product ID before any database operations that might detach it
    product_id = test_product.id

    # Create teams
    source_team = DBTeam(name="Source", admin_email="source@example.com", is_active=True)
    target_team = DBTeam(name="Target", admin_email="target@example.com", is_active=True)
    db.add_all([source_team, target_team])
    db.commit()

    # Associate product with source team
    source_team_product = DBTeamProduct(
        team_id=source_team.id,
        product_id=product_id
    )
    db.add(source_team_product)
    db.commit()

    response = client.post(
        f"/teams/{target_team.id}/merge",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "source_team_id": source_team.id,
            "conflict_resolution_strategy": "delete"
        }
    )

    assert response.status_code == 400
    assert "active product associations" in response.json()["detail"]
    assert product_id in response.json()["detail"]

    # Verify both teams still exist
    source_team_exists = db.query(DBTeam).filter(DBTeam.id == source_team.id).first()
    target_team_exists = db.query(DBTeam).filter(DBTeam.id == target_team.id).first()
    assert source_team_exists is not None
    assert target_team_exists is not None

    # Verify product association still exists
    source_team_product_exists = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == source_team.id,
        DBTeamProduct.product_id == product_id
    ).first()
    assert source_team_product_exists is not None