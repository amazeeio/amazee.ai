import pytest
from app.db.models import DBUser, DBTeam, DBProduct, DBTeamProduct, DBPrivateAIKey, DBLimitedResource
from app.core.limit_service import LimitService, DEFAULT_KEYS_PER_USER, DEFAULT_MAX_SPEND, DEFAULT_RPM_PER_KEY
from app.schemas.limits import ResourceType, LimitSource, OwnerType, LimitType, UnitType
from app.core.config import settings
from datetime import datetime, UTC
from unittest.mock import patch, AsyncMock

def test_create_user(client, test_admin, admin_token):
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "newuser@example.com",
            "password": "newpassword"
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    assert user_data["email"] == "newuser@example.com"
    assert user_data["is_admin"] is False
    assert "id" in user_data

def test_create_user_duplicate_email(client, test_user, admin_token, db):
    # Refresh test_user to ensure it's attached to the session
    test_user = db.merge(test_user)
    db.refresh(test_user)

    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": test_user.email,
            "password": "newpassword",
            "is_admin": False
        }
    )
    assert response.status_code == 400
    assert "Email already registered" in response.json()["detail"]

def test_get_users(client, admin_token, test_user):
    response = client.get(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    users = response.json()
    assert isinstance(users, list)
    assert len(users) >= 1
    assert any(user["email"] == test_user.email for user in users)

def test_get_user_by_id(client, admin_token, test_user):
    response = client.get(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == test_user.email
    assert user_data["id"] == test_user.id

def test_get_nonexistent_user(client, admin_token):
    response = client.get(
        "/users/99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "User not found"

def test_update_user(client, admin_token, test_user):
    response = client.put(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "updated@example.com",
            "is_admin": False,
            "is_active": True
        }
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == "updated@example.com"

def test_delete_user(client, admin_token, test_user):
    response = client.delete(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"

    # Verify user is actually deleted
    response = client.get(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404

def test_delete_user_unauthorized(client, test_user, test_token):
    response = client.delete(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403

def test_delete_team_member(client, admin_token, test_team_user):
    """
    Test deleting a user who is a member of a team and has no AI keys.

    GIVEN: User A is a member of Team 1 and has no AI keys
    WHEN: User A is deleted
    THEN: A 200 - Success is returned, User A is deleted
    """
    response = client.delete(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "User deleted successfully"

    # Verify user is actually deleted
    response = client.get(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404

@patch("httpx.AsyncClient.post", new_callable=AsyncMock)
def test_delete_team_member_with_ai_keys(mock_post, client, admin_token, test_team_user, test_region, db):
    """
    Test deleting a user who is a member of a team and has associated AI keys.

    GIVEN: User A is a member of Team 1 AND has associated AI Keys
    WHEN: User A is deleted
    THEN: A 400 - Invalid Request is returned
    """
    # Mock the LiteLLM API response
    mock_post.return_value.status_code = 200
    mock_post.return_value.json.return_value = {"key": "test-private-key-123"}
    mock_post.return_value.raise_for_status.return_value = None

    # Create an AI key for the team user
    ai_key = DBPrivateAIKey(
        database_name=f"test_db_{test_team_user.id}",
        name="Test AI Key",
        database_host="localhost",
        database_username="test_user",
        database_password="test_password",
        litellm_token="test-private-key-123",
        litellm_api_url="http://localhost:8000",
        owner_id=test_team_user.id,
        region_id=test_region.id,
        team_id=test_team_user.team_id
    )
    db.add(ai_key)
    db.commit()
    db.refresh(ai_key)

    # Try to delete the user
    response = client.delete(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 400
    assert "Cannot delete user with associated AI keys" in response.json()["detail"]

    # Verify user is not deleted
    response = client.get(
        f"/users/{test_team_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200

def test_create_user_by_team_admin(client, team_admin_token, test_team, db):
    """Test that a team admin can create a user in their own team"""
    # Get the team ID directly from the database to avoid detached instance issues
    team_id = db.query(DBTeam).filter(DBTeam.admin_email == "testteam@example.com").first().id

    # Create a new user in the team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "newteamuser@example.com",
            "password": "newpassword",
            "team_id": team_id
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    assert user_data["email"] == "newteamuser@example.com"
    assert user_data["is_admin"] is False
    assert user_data["team_id"] == team_id
    assert "id" in user_data

    # Verify the user is actually in the team in the database
    db_user = db.query(DBUser).filter(DBUser.id == user_data["id"]).first()
    assert db_user.team_id == team_id

    # Verify the user appears in the team's user list
    team_response = client.get(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert team_response.status_code == 200
    team_data = team_response.json()
    assert any(u["id"] == user_data["id"] for u in team_data["users"])

def test_create_user_in_other_team_by_team_admin(client, team_admin_token, db):
    """Test that a team admin cannot create a user in another team"""
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

    # Try to create a user in the second team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "newteamuser@example.com",
            "password": "newpassword",
            "team_id": team2.id
        }
    )
    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_create_user_in_team_by_system_admin(client, admin_token, test_team, db):
    """
    Test that a system admin can create a user in a specific team.

    GIVEN: The authenticated user is a system admin
    WHEN: They create a user in a specific team
    THEN: A 201 - Created is returned
    """
    team_id = test_team.id

    # Create a new user in the team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "newteamuser@example.com",
            "password": "newpassword",
            "team_id": team_id
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    assert user_data["email"] == "newteamuser@example.com"
    assert user_data["is_admin"] is False
    assert user_data["team_id"] == team_id
    assert "id" in user_data

    # Verify the user is actually in the team in the database
    db_user = db.query(DBUser).filter(DBUser.id == user_data["id"]).first()
    assert db_user.team_id == team_id

def test_create_read_only_user_by_team_admin(client, team_admin_token, test_team, db):
    """Test that a team admin can create a read-only user in their own team"""
    team_id = test_team.id

    # Create a new read-only user in the team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "newreadonly@example.com",
            "password": "newpassword",
            "team_id": team_id,
            "role": "read_only"
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    assert user_data["email"] == "newreadonly@example.com"
    assert user_data["is_admin"] is False
    assert user_data["role"] == "read_only"
    assert user_data["team_id"] == team_id
    assert "id" in user_data

    # Verify the user is actually in the team in the database
    db_user = db.query(DBUser).filter(DBUser.id == user_data["id"]).first()
    assert db_user.team_id == team_id
    assert db_user.role == "read_only"

    # Verify the user appears in the team's user list
    team_response = client.get(
        f"/teams/{team_id}",
        headers={"Authorization": f"Bearer {team_admin_token}"}
    )
    assert team_response.status_code == 200
    team_data = team_response.json()
    assert any(u["id"] == user_data["id"] for u in team_data["users"])

def test_create_user_with_invalid_role_by_team_admin(client, team_admin_token, test_team):
    """Test that a team admin cannot create a user with an invalid role"""
    team_id = test_team.id

    # Try to create a user with an invalid role
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "invalidrole@example.com",
            "password": "newpassword",
            "team_id": team_id,
            "role": "nonsense_role"
        }
    )
    assert response.status_code == 400
    assert "Invalid role" in response.json()["detail"]

def test_make_non_team_user_admin(client, admin_token, test_user, db):
    """
    Test that a system admin can make a non-team user an admin.

    GIVEN: The authenticated user is a system admin
    WHEN: They try to make a non-team user an admin
    THEN: A 200 success is returned and the user is updated
    """
    # Ensure test_user is not an admin and not in a team
    test_user = db.merge(test_user)
    test_user.is_admin = False
    test_user.team_id = None
    db.commit()
    db.refresh(test_user)

    # Update the user to make them an admin
    response = client.put(
        f"/users/{test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": test_user.email,
            "is_admin": True,
            "is_active": True
        }
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["is_admin"] is True

def test_make_team_member_admin_by_team_admin(client, team_admin_token, admin_token):
    """
    Test that a team admin cannot make a user an admin.

    GIVEN: A user is created not in a team
    WHEN: A team admin tries to make that user an admin
    THEN: A 403 - Forbidden is returned
    """
    # First create a user not in any team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "nonteamuser@example.com",
            "password": "newpassword"
        }
    )
    assert response.status_code == 201
    user_data = response.json()
    user_id = user_data["id"]

    # Try to make the user an admin
    response = client.put(
        f"/users/{user_id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "is_admin": True
        }
    )
    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]

def test_user_privilege_escalation(client, team_admin_token):
    """
    Test that a team admin cannot make a user an admin.

    GIVEN: A user is created not in a team
    WHEN: A team admin tries to make that user an admin
    THEN: A 403 - Forbidden is returned
    """
    user = client.post("/auth/register", json={
        "email": "testuser@example.com",
        "password": "testpassword"
    })
    assert user.status_code == 200
    user_data = user.json()
    user_id = user_data["id"]

    # Make the user an admin
    response = client.put(
        f"/users/{user_id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "is_admin": True
        }
    )
    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]

@patch('app.api.users.settings.ENABLE_LIMITS', True)
def test_create_user_with_limits_enabled(client, team_admin_token, test_team, db):
    """
    Test that a team cannot create more users when ENABLE_LIMITS is true and they have reached their limit.

    GIVEN: a team with users up to the limit, and ENABLE_LIMITS is true
    WHEN: the team tries to create another user
    THEN: a 402 payment required is returned
    """
    # Create a product with a specific user limit for testing
    user_count = 2
    test_product = DBProduct(
        id="prod_test_user_limit_enabled",
        name="Test Product User Limit Enabled",
        user_count=user_count,  # Specific limit for testing (including existing team admin)
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
    db.add(test_product)

    # Associate the product with the team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create one more user to reach the limit (team admin already exists)
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "user1@example.com",
            "password": "newpassword",
            "team_id": test_team.id
        }
    )
    assert response.status_code == 201

    # Try to create one more user (should fail)
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={
            "email": "newteamuser@example.com",
            "password": "newpassword",
            "team_id": test_team.id
        }
    )
    assert response.status_code == 402
    assert f"Team has reached their maximum user limit" in response.json()["detail"]


def test_create_user_creates_default_limits(client, admin_token, test_team, db):
    """
    Given: A new user is being created in a team
    When: The user creation endpoint is called
    Then: Default limits should be created for the user

    This test ensures that when a new user is created, default limits are automatically
    set up for the user using the limit service.
    """
    # Enable limits for this test
    settings.ENABLE_LIMITS = True

    # Ensure system default limits exist first
    from app.core.limit_service import setup_default_limits
    setup_default_limits(db)

    # Create a new user in the team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "newuser@example.com",
            "password": "newpassword",
            "team_id": test_team.id
        }
    )

    assert response.status_code == 201
    user_data = response.json()
    user_id = user_data["id"]

    # Verify the user was created
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    assert db_user is not None
    assert db_user.email == "newuser@example.com"
    assert db_user.team_id == user_data["team_id"]

    # Verify that default limits were created for the user
    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(db_user)

    # Should have user-specific limits for KEY only - BUDGET and RPM are inherited from team
    user_specific_limits = [limit for limit in user_limits if limit.owner_type == OwnerType.USER]

    # Find the user-specific KEY limit
    key_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.KEY), None)

    # Verify KEY limit exists and has correct values
    assert key_limit is not None
    assert key_limit.max_value == DEFAULT_KEYS_PER_USER
    assert key_limit.limited_by == LimitSource.DEFAULT
    assert key_limit.owner_type == OwnerType.USER
    assert key_limit.owner_id == user_id
    assert key_limit.limit_type == LimitType.CONTROL_PLANE
    assert key_limit.unit == UnitType.COUNT
    assert key_limit.current_value == 0.0

    # Verify that BUDGET and RPM limits are inherited from team (not user-specific)
    budget_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.BUDGET), None)
    rpm_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.RPM), None)
    assert budget_limit is None  # Should be inherited from team, not user-specific
    assert rpm_limit is None     # Should be inherited from team, not user-specific


def test_register_user_creates_default_limits(client, db):
    """
    Given: A new user is being registered via the auth endpoint
    When: The user registration endpoint is called
    Then: Default limits should be created for the user

    This test ensures that when a new user is registered, default limits are automatically
    set up for the user using the limit service.
    """
    # Enable limits for this test
    settings.ENABLE_LIMITS = True

    # Ensure system default limits exist first
    from app.core.limit_service import setup_default_limits
    setup_default_limits(db)

    # Register a new user
    response = client.post(
        "/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "newpassword"
        }
    )

    assert response.status_code == 200
    user_data = response.json()
    user_id = user_data["id"]

    # Verify the user was created
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    assert db_user is not None
    assert db_user.email == "newuser@example.com"

    # Verify that default limits were created for the user
    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(db_user)

    # Should have user-specific limits for KEY only - BUDGET and RPM are inherited from team
    user_specific_limits = [limit for limit in user_limits if limit.owner_type == OwnerType.USER]

    # Find the user-specific KEY limit
    key_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.KEY), None)

    # Verify KEY limit exists and has correct values
    assert key_limit is not None
    assert key_limit.max_value == DEFAULT_KEYS_PER_USER
    assert key_limit.limited_by == LimitSource.DEFAULT
    assert key_limit.owner_type == OwnerType.USER
    assert key_limit.owner_id == user_id

    # Verify that BUDGET and RPM limits are inherited from team (not user-specific)
    budget_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.BUDGET), None)
    rpm_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.RPM), None)
    assert budget_limit is None  # Should be inherited from team, not user-specific
    assert rpm_limit is None     # Should be inherited from team, not user-specific


def test_create_user_does_not_create_limits_when_disabled(client, admin_token, test_team, db):
    """
    Given: ENABLE_LIMITS is false
    When: A new user is created
    Then: No default limits should be created for the user
    """
    # Disable limits for this test
    settings.ENABLE_LIMITS = False

    # Create a new user in the team
    response = client.post(
        "/users/",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "email": "newuser@example.com",
            "password": "newpassword",
            "team_id": test_team.id
        }
    )

    assert response.status_code == 201
    user_data = response.json()
    user_id = user_data["id"]

    # Verify the user was created
    db_user = db.query(DBUser).filter(DBUser.id == user_id).first()
    assert db_user is not None

    # Verify that no user-specific limits were created
    user_limits = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.USER,
        DBLimitedResource.owner_id == user_id
    ).all()

    assert len(user_limits) == 0


def test_sign_in_creates_user_with_default_limits(client, db):
    """
    Given: A new user signs in via the sign-in endpoint (auto-creation)
    When: The sign-in endpoint creates a new user and team
    Then: Default limits should be created for the user

    This test ensures that when a new user is auto-created during sign-in,
    default limits are automatically set up for the user.
    """
    # Enable limits for this test
    settings.ENABLE_LIMITS = True

    # Ensure system default limits exist first
    from app.core.limit_service import setup_default_limits
    setup_default_limits(db)

    # First register a user
    register_response = client.post(
        "/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "newpassword"
        }
    )
    assert register_response.status_code == 200

    # Then login with the user
    response = client.post(
        "/auth/login",
        data={
            "username": "newuser@example.com",
            "password": "newpassword"
        }
    )

    assert response.status_code == 200
    token_data = response.json()
    assert "access_token" in token_data

    # Find the created user
    db_user = db.query(DBUser).filter(DBUser.email == "newuser@example.com").first()
    assert db_user is not None

    # Verify that default limits were created for the user
    limit_service = LimitService(db)
    user_limits = limit_service.get_user_limits(db_user)

    # Should have user-specific limits for KEY only - BUDGET and RPM are inherited from team
    user_specific_limits = [limit for limit in user_limits if limit.owner_type == OwnerType.USER]

    # Find the user-specific KEY limit
    key_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.KEY), None)

    # Verify KEY limit exists and has correct values
    assert key_limit is not None
    assert key_limit.max_value == DEFAULT_KEYS_PER_USER
    assert key_limit.limited_by == LimitSource.DEFAULT
    assert key_limit.owner_type == OwnerType.USER
    assert key_limit.owner_id == db_user.id

    # Verify that BUDGET and RPM limits are inherited from team (not user-specific)
    budget_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.BUDGET), None)
    rpm_limit = next((limit for limit in user_specific_limits if limit.resource == ResourceType.RPM), None)
    assert budget_limit is None  # Should be inherited from team, not user-specific
    assert rpm_limit is None     # Should be inherited from team, not user-specific
