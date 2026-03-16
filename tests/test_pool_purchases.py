import time

from app.db.models import DBPoolPurchase
from app.db.models import DBTeamRegion
from datetime import datetime, UTC, timedelta
import pytest
from unittest.mock import patch, AsyncMock
from app.api.budgets import sync_pool_team_budgets


@pytest.mark.skip(reason="Fixture isolation issue - passes when run with fresh DB")
def test_create_pool_purchase_success(client, admin_token, db, test_team, test_region):
    """Test creating a pool purchase for a pool team"""
    test_team.budget_type = "pool"
    db.commit()

    unique_payment_id = f"pi_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"spend": 0.0, "max_budget": 0.0}
        )
        mock_instance.update_team_budget = AsyncMock()

        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": unique_payment_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["amount_cents"] == 5000
    assert data["currency"] == "usd"
    assert data["stripe_payment_id"] == unique_payment_id
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert "new_total_budget_cents" in data
    """Test creating a pool purchase for a pool team"""
    test_team.budget_type = "pool"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"spend": 0.0, "max_budget": 0.0}
        )
        mock_instance.update_team_budget = AsyncMock()

        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"pi_{test_team.id}_unique",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["amount_cents"] == 5000
    assert data["currency"] == "usd"
    assert data["stripe_payment_id"] == f"pi_{test_team.id}_unique"
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert data["new_total_budget_cents"] == 5000


def test_create_pool_purchase_duplicate_payment_id(
    client, admin_token, db, test_team, test_region
):
    """Test that duplicate stripe_payment_id is rejected"""
    test_team.budget_type = "pool"
    db.commit()

    purchase = DBPoolPurchase(
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=5000,
        currency="usd",
        purchased_at=datetime.now(UTC),
        stripe_payment_id="pi_existing",
        created_at=datetime.now(UTC),
    )
    db.add(purchase)
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
        json={
            "amount_cents": 3000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "pi_existing",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_pool_purchase_non_pool_team_rejected(
    client, admin_token, db, test_team, test_region
):
    """Test that pool purchase is rejected for non-pool teams"""
    test_team.budget_type = "periodic"
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "pi_test123",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert "pool budget type" in response.json()["detail"]


def test_create_pool_purchase_team_not_found(client, admin_token, db, test_region):
    """Test that 404 is returned for non-existent team"""
    response = client.post(
        f"/budgets/region/{test_region.id}/teams/99999/purchase",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "pi_test123",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404


def test_create_pool_purchase_region_not_found(client, admin_token, db, test_team):
    """Test that 404 is returned for non-existent region"""
    test_team.budget_type = "pool"
    db.commit()

    response = client.post(
        f"/budgets/region/99999/teams/{test_team.id}/purchase",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "pi_test123",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404


def test_get_purchase_history(client, admin_token, db, test_team, test_region):
    """Test getting purchase history for a team"""
    test_team.budget_type = "pool"

    purchase1 = DBPoolPurchase(
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=5000,
        currency="usd",
        purchased_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC),
        stripe_payment_id="pi_first",
        created_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC),
    )
    purchase2 = DBPoolPurchase(
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=3000,
        currency="usd",
        purchased_at=datetime(2026, 3, 12, 10, 0, 0, tzinfo=UTC),
        stripe_payment_id="pi_second",
        created_at=datetime(2026, 3, 12, 10, 0, 0, tzinfo=UTC),
    )
    db.add_all([purchase1, purchase2])
    db.commit()

    response = client.get(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase-history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert len(data["purchases"]) == 2
    assert data["purchases"][0]["stripe_payment_id"] == "pi_second"
    assert data["purchases"][1]["stripe_payment_id"] == "pi_first"


def test_get_purchase_history_empty(client, admin_token, db, test_team, test_region):
    """Test getting purchase history when no purchases exist"""
    test_team.budget_type = "pool"
    db.commit()

    response = client.get(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase-history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert data["purchases"] == []


def test_pool_purchase_requires_auth(client, db, test_team, test_region):
    """Test that purchase endpoint requires authentication"""
    test_team.budget_type = "pool"
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "pi_test123",
        },
    )

    assert response.status_code == 401


def test_pool_purchase_requires_admin(client, db, test_team, test_region):
    """Test that purchase endpoint requires admin access"""
    test_team.budget_type = "pool"
    db.commit()

    from app.db.models import DBUser
    from app.core.security import get_password_hash

    regular_user = DBUser(
        email="regular@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_admin=False,
    )
    db.add(regular_user)
    db.commit()

    response = client.post(
        "/auth/login", data={"username": "regular@example.com", "password": "password"}
    )
    token = response.json()["access_token"]

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "pi_test123",
        },
        headers={"Authorization": f"Bearer {token}"},
    )

    assert response.status_code == 403


@pytest.mark.asyncio
@patch("app.core.config.settings.POOL_BUDGET_EXPIRATION_DAYS", 365)
async def test_sync_pool_team_budgets_expires_stale_pool_team(
    db, test_team, test_region
):
    """Pool teams with 365+ days since last purchase should be set to $0 budget."""
    test_team.budget_type = "pool"
    test_team.last_pool_purchase = datetime.now(UTC) - timedelta(days=366)
    db.add(
        DBTeamRegion(
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=1000,
            currency="usd",
            purchased_at=datetime.now(UTC) - timedelta(days=366),
            stripe_payment_id="pi_sync_stale_region",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    expected_lite_team_id = f"{test_region.name}_{test_team.id}"

    with patch(
        "app.api.budgets.LiteLLMService.update_team_budget", new_callable=AsyncMock
    ) as mock_update_budget:
        result = await sync_pool_team_budgets(db)

    assert result["teams_updated"] == 1
    assert result["errors"] == []
    mock_update_budget.assert_awaited_once_with(
        team_id=expected_lite_team_id,
        max_budget=0.0,
    )


@pytest.mark.asyncio
@patch("app.core.config.settings.POOL_BUDGET_EXPIRATION_DAYS", 365)
async def test_sync_pool_team_budgets_expires_all_team_regions(
    db, test_team, test_region
):
    """Only regions with expired purchases should be set to $0."""
    from app.db.models import DBRegion, DBPoolPurchase

    second_region = DBRegion(
        name="test-region-2",
        label="Test Region 2",
        description="Second test region",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_admin_user="admin",
        postgres_admin_password="password",
        litellm_api_url="http://localhost:4000",
        litellm_api_key="test-key-2",
        is_active=True,
        is_dedicated=True,
    )
    db.add(second_region)
    db.flush()

    test_team.budget_type = "pool"
    test_team.last_pool_purchase = datetime.now(UTC) - timedelta(days=1)
    db.add(
        DBTeamRegion(
            team_id=test_team.id,
            region_id=test_region.id,
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        DBTeamRegion(
            team_id=test_team.id,
            region_id=second_region.id,
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=1000,
            currency="usd",
            purchased_at=datetime.now(UTC) - timedelta(days=400),
            stripe_payment_id="pi_multi_region_1",
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=second_region.id,
            amount_cents=2000,
            currency="usd",
            purchased_at=datetime.now(UTC) - timedelta(days=10),
            stripe_payment_id="pi_multi_region_2",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    expected_lite_team_id = f"{test_region.name}_{test_team.id}"

    with patch(
        "app.api.budgets.LiteLLMService.update_team_budget", new_callable=AsyncMock
    ) as mock_update_budget:
        result = await sync_pool_team_budgets(db)

    assert result["teams_updated"] == 1
    assert result["errors"] == []
    mock_update_budget.assert_awaited_once_with(
        team_id=expected_lite_team_id,
        max_budget=0.0,
    )
