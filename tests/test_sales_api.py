"""
Tests for the consolidated sales API endpoint.
"""
import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import patch, AsyncMock
from sqlalchemy.orm import Session
from app.db.models import DBTeam, DBProduct, DBTeamProduct, DBPrivateAIKey, DBRegion, DBUser


@pytest.fixture
def test_ai_key(db: Session, test_team: DBTeam, test_region: DBRegion) -> DBPrivateAIKey:
    """Create a test AI key."""
    ai_key = DBPrivateAIKey(
        database_name="test-db",
        name="Test Key",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token",
        litellm_api_url="https://test-litellm.com",
        owner_id=None,
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(ai_key)
    db.commit()
    db.refresh(ai_key)
    return ai_key


@pytest.fixture
def test_always_free_team(db: Session) -> DBTeam:
    """Create a test always-free team."""
    team = DBTeam(
        name="Always Free Team",
        admin_email="free@test.com",
        is_active=True,
        is_always_free=True,
        created_at=datetime.now(UTC) - timedelta(days=45),  # 45 days ago
        last_payment=None
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@pytest.fixture
def test_paid_team(db: Session) -> DBTeam:
    """Create a test team with payment history."""
    team = DBTeam(
        name="Paid Team",
        admin_email="paid@test.com",
        is_active=True,
        is_always_free=False,
        created_at=datetime.now(UTC) - timedelta(days=60),  # 60 days ago
        last_payment=datetime.now(UTC) - timedelta(days=5)  # 5 days ago
    )
    db.add(team)
    db.commit()
    db.refresh(team)
    return team


@pytest.fixture
def mock_litellm_response():
    """Mock LiteLLM API response."""
    return {
        "info": {
            "spend": 25.50,
            "expires": "2024-12-31T23:59:59Z",
            "created_at": "2024-01-01T00:00:00Z",
            "updated_at": "2024-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "monthly",
            "budget_reset_at": "2024-02-01T00:00:00Z"
        }
    }


@pytest.fixture
def test_user_owned_ai_key(db: Session, test_team_user: DBUser, test_region: DBRegion) -> DBPrivateAIKey:
    """Create a test AI key owned by a team member."""
    ai_key = DBPrivateAIKey(
        database_name="user-db",
        name="User Key",
        database_host="user-host",
        database_username="user-user",
        database_password="user-pass",
        litellm_token="user-token",
        litellm_api_url="https://user-litellm.com",
        owner_id=test_team_user.id,
        team_id=None,  # Not team-owned, user-owned
        region_id=test_region.id
    )
    db.add(ai_key)
    db.commit()
    db.refresh(ai_key)
    return ai_key


def test_list_teams_for_sales_requires_admin(client, test_team):
    """Test that only system admins can access the sales endpoint."""
    response = client.get("/teams/sales/list-teams")
    assert response.status_code == 401  # Unauthorized


def test_list_teams_for_sales_success(client, admin_token, test_team, test_product,
                                      test_region, test_ai_key, mock_litellm_response, db):
    """Test successful retrieval of sales data."""
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = mock_litellm_response

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    assert "teams" in data
    assert len(data["teams"]) == 1

    team_data = data["teams"][0]
    assert team_data["id"] == test_team.id
    assert team_data["name"] == test_team.name
    assert team_data["admin_email"] == test_team.admin_email
    assert team_data["is_always_free"] == False
    assert len(team_data["products"]) == 1
    assert team_data["products"][0]["id"] == test_product.id
    assert team_data["products"][0]["name"] == test_product.name
    assert team_data["products"][0]["active"] == True
    assert len(team_data["regions"]) == 1
    assert team_data["regions"][0] == test_region.name
    assert team_data["total_spend"] == 25.50
    assert team_data["trial_status"] == "Active Product"


def test_always_free_team_trial_status(client, admin_token, test_always_free_team,
                                       test_product, test_region, test_ai_key, db):
    """Test that always-free teams show correct trial status."""
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_always_free_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    assert team_data["trial_status"] == "Always Free"


def test_paid_team_trial_status(client, admin_token, test_paid_team,
                                test_product, test_region, test_ai_key, db):
    """Test that teams with payment history show correct trial status."""
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_paid_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    assert "teams" in data
    team_data = data["teams"][0]
    assert team_data["trial_status"] == "Active Product"


def test_team_without_products(client, admin_token, test_team, test_region, test_ai_key, db):
    """Test team without any products shows trial status based on creation date."""
    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    assert "teams" in data
    team_data = data["teams"][0]
    # Team without products should show trial status based on creation date
    assert team_data["trial_status"] == "30 days left"
    assert len(team_data["products"]) == 0


def test_team_with_multiple_ai_keys(client, admin_token, test_team, test_product,
                                    test_region, test_ai_key, db):
    """Test team with multiple AI keys aggregates spend correctly."""
    # Create second AI key
    ai_key2 = DBPrivateAIKey(
        database_name="test-db-2",
        name="Test Key 2",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-2",
        litellm_api_url="https://test-litellm.com",
        owner_id=None,
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(ai_key2)
    db.commit()

    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock LiteLLM service with different spend values
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.side_effect = [
            {"info": {"spend": 25.50}},
            {"info": {"spend": 15.25}}
        ]

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    assert team_data["total_spend"] == 40.75  # 25.50 + 15.25


def test_litellm_service_error_handling(client, admin_token, test_team, test_product,
                                        test_region, test_ai_key, db):
    """Test that LiteLLM service errors don't break the entire response."""
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock LiteLLM service to raise an exception
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.side_effect = Exception("LiteLLM service error")

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    # Should still succeed but with 0 spend
    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    assert team_data["total_spend"] == 0.0


def test_expired_trial_status(client, admin_token, test_team, test_region, test_ai_key, db):
    """Test that expired trials show correct status."""
    # Update team to be older than 30 days (no products, so should show expired)
    test_team.created_at = datetime.now(UTC) - timedelta(days=35)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    assert team_data["trial_status"] == "Expired"


def test_list_teams_for_sales_includes_user_owned_keys(client, admin_token, test_team, test_product,
                                                       test_region, test_ai_key, test_team_user,
                                                       test_user_owned_ai_key, mock_litellm_response, db):
    """Test that sales data includes both team-owned and user-owned AI keys."""
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Mock LiteLLM service to return different spend for each key
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        # Return different spend values for team key vs user key
        mock_get_info.side_effect = [
            {"info": {"spend": 25.50}},  # Team key spend
            {"info": {"spend": 15.25}}   # User key spend
        ]

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    assert "teams" in data
    assert len(data["teams"]) == 1

    team_data = data["teams"][0]
    assert team_data["id"] == test_team.id

    # Should include both keys in total spend (25.50 + 15.25 = 40.75)
    assert team_data["total_spend"] == 40.75

    # Should include region from both keys
    assert len(team_data["regions"]) == 1
    assert team_data["regions"][0] == test_region.name


def test_team_with_15_days_remaining(client, admin_token, test_team, test_region, test_ai_key, db):
    """Test that teams with 15 days remaining show correct format."""
    # Update team to be 15 days old
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    # Should show exact days remaining, not "In Progress"
    assert team_data["trial_status"] == "15 days left"


def test_team_with_7_days_remaining(client, admin_token, test_team, test_region, test_ai_key, db):
    """Test that teams with 7 days remaining show correct format."""
    # Update team to be 23 days old (7 days remaining)
    test_team.created_at = datetime.now(UTC) - timedelta(days=23)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    # Should show exact days remaining
    assert team_data["trial_status"] == "7 days left"


def test_team_with_last_payment_days_calculation(client, admin_token, test_team, test_region, test_ai_key, db):
    """Test that teams with last_payment calculate days remaining from payment date."""
    # Set last_payment to 10 days ago (so 20 days remaining from payment)
    test_team.last_payment = datetime.now(UTC) - timedelta(days=10)
    db.commit()

    # Mock LiteLLM service
    with patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock) as mock_get_info:
        mock_get_info.return_value = {"info": {"spend": 0.0}}

        response = client.get(
            "/teams/sales/list-teams",
            headers={"Authorization": f"Bearer {admin_token}"}
        )

    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]
    # Should calculate from last_payment date (30 - 10 = 20 days remaining)
    assert team_data["trial_status"] == "20 days left"


@patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock)
def test_list_teams_for_sales_unreachable_endpoints_logging(mock_get_info, client, admin_token, test_team, test_product,
                                                           test_region, test_ai_key, db):
    """
    Given a team with AI keys in a region where LiteLLM endpoint is unreachable
    When calling list_teams_for_sales
    Then the function should default spend to 0 and still return successful response
    """
    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a second AI key in the same region to test multiple keys
    ai_key2 = DBPrivateAIKey(
        database_name="test-db-2",
        name="Test Key 2",
        database_host="test-host",
        database_username="test-user",
        database_password="test-pass",
        litellm_token="test-token-2",
        litellm_api_url="https://test-litellm.com",
        owner_id=None,
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(ai_key2)
    db.commit()

    # Mock LiteLLM service to raise an exception (simulating unreachable endpoint)
    mock_get_info.side_effect = Exception("Connection timeout")

    response = client.get(
        "/teams/sales/list-teams",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Should still succeed despite LiteLLM errors
    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]

    # Spend should default to 0 for failed litellm calls
    assert team_data["total_spend"] == 0.0


@patch('app.services.litellm.LiteLLMService.get_key_info', new_callable=AsyncMock)
def test_list_teams_for_sales_multiple_unreachable_regions(mock_get_info, client, admin_token, test_team, test_product,
                                                          test_region, test_ai_key, db):
    """
    Given a team with AI keys in multiple regions where some endpoints are unreachable
    When calling list_teams_for_sales
    Then the function should default spend to 0 and include all regions in response
    """
    # Create a second region
    region2 = DBRegion(
        name="Test Region 2",
        postgres_host="test-host-2",
        postgres_port=5432,
        postgres_admin_user="test-user-2",
        postgres_admin_password="test-pass-2",
        litellm_api_url="https://test-litellm-2.com",
        litellm_api_key="test-key-2",
        is_active=True
    )
    db.add(region2)
    db.commit()

    # Create team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create AI key in second region
    ai_key2 = DBPrivateAIKey(
        database_name="test-db-2",
        name="Test Key 2",
        database_host="test-host-2",
        database_username="test-user-2",
        database_password="test-pass-2",
        litellm_token="test-token-2",
        litellm_api_url="https://test-litellm-2.com",
        owner_id=None,
        team_id=test_team.id,
        region_id=region2.id
    )
    db.add(ai_key2)
    db.commit()

    # Mock LiteLLM service to raise different exceptions for different regions
    mock_get_info.side_effect = [
        Exception("Connection timeout"),  # First region fails
        Exception("Service unavailable")  # Second region fails
    ]

    response = client.get(
        "/teams/sales/list-teams",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    # Should still succeed despite LiteLLM errors
    assert response.status_code == 200
    data = response.json()
    team_data = data["teams"][0]

    # Spend should default to 0 for failed litellm calls
    assert team_data["total_spend"] == 0.0

    # Should include both regions in the response
    assert len(team_data["regions"]) == 2
    assert test_region.name in team_data["regions"]
    assert region2.name in team_data["regions"]
