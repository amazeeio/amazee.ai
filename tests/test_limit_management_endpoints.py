import pytest
from datetime import datetime, UTC
from fastapi.testclient import TestClient
from app.db.models import DBLimitedResource
from app.schemas.limits import LimitType, ResourceType, UnitType, OwnerType, LimitSource


def test_admin_can_overwrite_any_limit(client: TestClient, admin_token, test_team):
    """
    Given: System admin credentials
    When: Using overwrite_limit API with MANUAL source
    Then: Should successfully override any existing limit
    """
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "user",
            "limit_type": "control_plane",
            "unit": "count",
            "max_value": 10.0,
            "current_value": 3.0
            # limited_by and set_by are automatically set by the API
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["owner_type"] == "team"
    assert data["resource"] == "user"
    assert data["max_value"] == 10.0
    assert data["limited_by"] == "manual"
    assert data["set_by"] == "admin@example.com"  # Should be set to admin user's email


def test_admin_can_reset_team_limits(client: TestClient, admin_token, test_team):
    """
    Given: System admin credentials
    When: Calling reset_team_limits API
    Then: Should successfully reset all limits for the team
    """
    response = client.post(
        f"/limits/teams/{test_team.id}/reset",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == test_team.id
    assert "limits" in data


def test_admin_can_reset_single_limit(client: TestClient, admin_token, test_team, db):
    """
    Given: System admin credentials
    When: Calling reset_limit API for specific resource
    Then: Should successfully reset that specific limit
    """
    # Create a limit first
    limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )
    db.add(limit)
    db.commit()

    response = client.post(
        f"/limits/reset",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "user"
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["resource"] == "user"
    assert data["owner_type"] == "team"


def test_non_admin_cannot_access_limit_apis(client: TestClient, test_token, test_team):
    """
    Given: Non-admin user credentials
    When: Attempting to call limit management APIs
    Then: Should return 403 Forbidden
    """
    # Test overwrite limit
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "user",
            "limit_type": "control_plane",
            "unit": "count",
            "max_value": 10.0,
            "current_value": 3.0
        }
    )

    assert response.status_code == 403

    # Test reset team limits
    response = client.post(
        f"/limits/teams/{test_team.id}/reset",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403

    # Test reset single limit
    response = client.post(
        f"/limits/reset",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "user"
        }
    )

    assert response.status_code == 403


def test_get_team_limits_api_returns_all_limits(client: TestClient, admin_token, test_team, db):
    """
    Given: Team with various limits
    When: Calling GET /teams/{team_id}/limits
    Then: Should return all effective limits for the team
    """
    # Create some limits for the team
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )
    key_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.DEFAULT,
        created_at=datetime.now(UTC)
    )
    db.add(user_limit)
    db.add(key_limit)
    db.commit()

    response = client.get(
        f"/limits/teams/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == test_team.id
    assert len(data["limits"]) == 2

    # Check that we have both limits
    resources = [limit["resource"] for limit in data["limits"]]
    assert "user" in resources
    assert "ai_key" in resources


def test_api_always_creates_manual_limits(client: TestClient, admin_token, test_team):
    """
    Given: Admin using the API to set limits
    When: Calling overwrite_limit endpoint
    Then: Should always create MANUAL limits with admin's email as set_by
    """
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "user",
            "limit_type": "control_plane",
            "unit": "count",
            "max_value": 8.0,
            "current_value": 3.0
        }
    )
    assert response.status_code == 200

    data = response.json()
    assert data["limited_by"] == "manual"
    assert data["set_by"] == "admin@example.com"


def test_api_automatically_handles_manual_limits(client: TestClient, admin_token, test_team):
    """
    Given: Admin using the API
    When: Creating any limit via the API
    Then: Should automatically be MANUAL with set_by populated from current user
    """
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "ai_key",
            "limit_type": "control_plane",
            "unit": "count",
            "max_value": 15.0,
            "current_value": 5.0
        }
    )

    assert response.status_code == 200
    data = response.json()
    assert data["limited_by"] == "manual"
    assert data["set_by"] == "admin@example.com"  # Automatically set from current user


def test_get_user_limits_api(client: TestClient, admin_token, test_team, test_team_user, db):
    """
    Given: User with team limits and individual overrides
    When: Calling GET /users/{user_id}/limits
    Then: Should return effective limits for the user
    """
    # Create team limit
    team_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC)
    )

    # Create user override
    user_limit = DBLimitedResource(
        limit_type=LimitType.CONTROL_PLANE,
        resource=ResourceType.KEY,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=3.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.MANUAL,
        set_by="admin@example.com",
        created_at=datetime.now(UTC)
    )

    db.add(team_limit)
    db.add(user_limit)
    db.commit()

    # Capture IDs before session closes
    team_id = test_team.id
    user_id = test_team_user.id

    response = client.get(
        f"/limits/users/{user_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == team_id
    assert len(data["limits"]) == 1

    # Should return user-specific limit, not team limit
    limit = data["limits"][0]
    assert limit["resource"] == "ai_key"
    assert limit["max_value"] == 10.0
    assert limit["limited_by"] == "manual"


def test_validation_errors_for_invalid_data(client: TestClient, admin_token, test_team):
    """
    Given: Admin credentials
    When: Sending invalid data to limit APIs
    Then: Should return appropriate validation errors
    """
    # Test invalid owner_type
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "invalid",
            "owner_id": test_team.id,
            "resource_type": "user",
            "limit_type": "control_plane",
            "unit": "count",
            "max_value": 10.0,
            "current_value": 3.0
        }
    )
    assert response.status_code == 422  # Validation error

    # Test invalid resource_type
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource_type": "invalid_resource",
            "limit_type": "control_plane",
            "unit": "count",
            "max_value": 10.0,
            "current_value": 3.0
        }
    )
    assert response.status_code == 422  # Validation error
