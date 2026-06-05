from unittest.mock import AsyncMock, patch

import pytest
from app.core.roles import UserRole
from datetime import UTC, datetime, timedelta
from sqlalchemy.exc import IntegrityError

from app.core.security import get_password_hash
from app.db.models import (
    BudgetType,
    DBPoolPurchase,
    DBPrivateAIKey,
    DBRegion,
    DBSpendCap,
    DBTeam,
    DBTeamRegion,
    DBUser,
)


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
def test_get_team_spend_by_region(
    mock_get_key_info,
    client,
    team_admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    team_key = DBPrivateAIKey(
        name="team-key",
        litellm_token="team-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    user_key = DBPrivateAIKey(
        name="user-key",
        litellm_token="user-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.add(user_key)
    db.commit()

    mock_get_key_info.side_effect = [
        {
            "info": {
                "spend": 12.5,
                "max_budget": 50.0,
                "prompt_tokens": 100,
                "completion_tokens": 40,
                "total_tokens": 140,
            }
        },
        {
            "info": {
                "spend": 7.5,
                "max_budget": 25.0,
                "prompt_tokens": 60,
                "completion_tokens": 20,
                "total_tokens": 80,
            }
        },
    ]

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert data["total_spend"] == 20.0
    assert data["total_budget"] == 75.0
    assert data["total_prompt_tokens"] == 160
    assert data["total_completion_tokens"] == 60
    assert data["total_tokens"] == 220
    assert data["key_count"] == 2


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
def test_get_user_spend_by_region(
    mock_get_key_info, client, team_admin_token, test_team_user, test_region, db
):
    key = DBPrivateAIKey(
        name="user-only-key",
        litellm_token="user-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team_user.team_id,
    )
    db.add(key)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {
            "spend": 11.25,
            "max_budget": 40.0,
            "prompt_tokens": 200,
            "completion_tokens": 50,
            "total_tokens": 250,
        }
    }

    response = client.get(
        f"/spend/{test_region.id}/user/{test_team_user.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["user_id"] == test_team_user.id
    assert data["total_spend"] == 11.25
    assert data["total_prompt_tokens"] == 200
    assert data["total_completion_tokens"] == 50
    assert data["total_tokens"] == 250
    assert data["key_count"] == 1
    assert data["keys"][0]["key_id"] == key.id
    assert data["keys"][0]["prompt_tokens"] == 200
    assert data["keys"][0]["completion_tokens"] == 50
    assert data["keys"][0]["total_tokens"] == 250


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
def test_key_spend_alias(
    mock_get_key_info, client, team_admin_token, test_team_user, test_region, db
):
    key = DBPrivateAIKey(
        name="alias-key",
        litellm_token="alias-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team_user.team_id,
    )
    db.add(key)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {
            "spend": 10.5,
            "expires": "2026-12-31T23:59:59Z",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "max_budget": 100.0,
            "budget_duration": "1mo",
            "budget_reset_at": "2026-02-01T00:00:00Z",
            "prompt_tokens": 1200,
            "completion_tokens": 300,
            "total_tokens": 1500,
        }
    }

    response = client.get(
        f"/spend/{test_region.id}/key/{key.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["spend"] == 10.5
    assert data["max_budget"] is None
    assert data["prompt_tokens"] == 1200
    assert data["completion_tokens"] == 300
    assert data["total_tokens"] == 1500


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
def test_key_spend_alias_uses_configured_cap_for_no_purchase_pool_team(
    mock_get_key_info, client, admin_token, test_team, test_region, db
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    key = DBPrivateAIKey(
        name="pool-no-purchase-key",
        litellm_token="pool-no-purchase-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team.id,
            key_id=key.id,
            max_budget=11.0,
            budget_duration="1mo",
        )
    )
    db.commit()

    mock_get_key_info.return_value = {
        "info": {
            "spend": 0.1,
            "expires": "2026-12-31T23:59:59Z",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "max_budget": 0.0,
            "budget_duration": "1mo",
            "budget_reset_at": "2026-02-01T00:00:00Z",
        }
    }

    response = client.get(
        f"/spend/{test_region.id}/key/{key.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["max_budget"] == 11.0


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_get_team_spend_uses_configured_caps_for_no_purchase_pool_team(
    mock_get_team_info, client, admin_token, test_team, test_region, db
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    key = DBPrivateAIKey(
        name="team-no-purchase-key",
        litellm_token="team-no-purchase-key-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()
    db.add_all(
        [
            DBSpendCap(
                scope="team",
                region_id=test_region.id,
                team_id=test_team.id,
                max_budget=5.0,
                budget_duration="1mo",
            ),
            DBSpendCap(
                scope="key",
                region_id=test_region.id,
                team_id=test_team.id,
                key_id=key.id,
                max_budget=11.0,
                budget_duration="1mo",
            ),
        ]
    )
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"spend": 0.0, "max_budget": 0.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key.name},
                "spend": 0.0,
                "max_budget": 0.0,
                "user_id": None,
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["total_budget"] == 5.0
    assert data["keys"][0]["max_budget"] == 11.0


@patch("app.api.spend.LiteLLMService.get_user_info", new_callable=AsyncMock)
def test_get_user_spend_uses_member_or_key_cap_for_no_purchase_pool_team(
    mock_get_user_info, client, admin_token, test_team, test_region, db
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    user = DBUser(
        email="pool-no-purchase-user@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        team_id=test_team.id,
        role=UserRole.TEAM_ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    key_with_key_cap = DBPrivateAIKey(
        name="pool-user-key-cap",
        litellm_token="pool-user-key-cap-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    key_with_member_cap = DBPrivateAIKey(
        name="pool-user-member-cap",
        litellm_token="pool-user-member-cap-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    db.add_all([key_with_key_cap, key_with_member_cap])
    db.commit()

    db.add_all(
        [
            DBSpendCap(
                scope="team_member",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user.id,
                max_budget=7.0,
                budget_duration="1mo",
            ),
            DBSpendCap(
                scope="key",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user.id,
                key_id=key_with_key_cap.id,
                max_budget=11.0,
                budget_duration="1mo",
            ),
        ]
    )
    db.commit()

    mock_get_user_info.return_value = {
        "user_info": {"spend": 0.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key_with_key_cap.name},
                "user_id": str(user.id),
                "spend": 0.0,
                "max_budget": 0.0,
            },
            {
                "metadata": {"amazeeai_private_ai_key_name": key_with_member_cap.name},
                "user_id": str(user.id),
                "spend": 0.0,
                "max_budget": 0.0,
            },
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/user/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    max_budget_by_name = {k["key_name"]: k["max_budget"] for k in data["keys"]}
    assert max_budget_by_name[key_with_key_cap.name] == 11.0
    assert max_budget_by_name[key_with_member_cap.name] == 7.0


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_endpoint(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.commit()
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 12.5, "budget_duration": "1mo"}
    }
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 12.5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "team"
    assert data["team_id"] == test_team.id
    assert data["max_budget"] == 12.5
    assert data["budget_duration"] == "1mo"
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "365d"


@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_rejects_manual_cap_for_periodic_team(
    mock_update_team_budget,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.PERIODIC
    test_team.require_purchase_for_requests = False
    db.commit()

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        json={"max_budget": 42.0},
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 400
    assert "not allowed for periodic teams" in response.json()["detail"].lower()
    mock_update_team_budget.assert_not_awaited()


@patch("app.api.spend.invalidate_user_spend_cache")
@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_invalidates_user_spend_cache_for_team_members(
    mock_update_team_budget,
    mock_get_team_info,
    mock_invalidate_user_spend_cache,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.commit()
    team_user = DBUser(
        email="cache-team-user@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        team_id=test_team.id,
    )
    db.add(team_user)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 12.5, "budget_duration": "1mo"}
    }
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 12.5},
    )
    assert response.status_code == 200
    invalidated_emails = {
        call.args[1] for call in mock_invalidate_user_spend_cache.call_args_list
    }
    assert "cache-team-user@example.com" in invalidated_emails


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_allows_cap_above_pool_purchases(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pool-team-cap-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 50.0, "budget_duration": "365d"}
    }

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_invoice_budget_allows_any_value_for_dedicated_team(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    """Periodic teams reject manual team budget updates, including dedicated teams."""
    test_team.budget_type = "periodic"
    test_team.require_purchase_for_requests = False
    test_team.hide_public_regions = True
    db.add(test_team)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 100.0, "budget_duration": "365d"}
    }
    mock_update_team_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 100.0},
    )
    assert response.status_code == 400, response.json()
    assert "not allowed for periodic teams" in response.json()["detail"].lower()
    mock_update_team_budget.assert_not_awaited()


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_pool_budget_allows_setting_cap_before_first_purchase(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    db.add(test_team)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 0.0, "budget_duration": "365d"}
    }
    mock_update_team_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_team_budget.assert_awaited_once()
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.team_id == test_team.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.max_budget == 60.0


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_pool_budget_returns_configured_cap_before_first_purchase(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 0.0, "budget_duration": "365d"}
    }
    mock_update_team_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 11.0},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["max_budget"] == 11.0


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_prepaid_pool_budget_for_dedicated_team_clamps_before_purchase(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    test_team.hide_public_regions = True
    db.add(test_team)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 0.0, "budget_duration": "365d"}
    }
    mock_update_team_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 100.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["max_budget"] == 0.0
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "365d"


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_pool_team_budget_uses_pool_duration(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pool-team-duration-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 12.5, "budget_duration": "365d"}
    }
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 12.5},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["max_budget"] == 12.5
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "365d"
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.team_id == test_team.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.budget_duration == "1mo"
    assert cap.month_anchor == datetime.now(UTC).date().replace(day=1)
    assert cap.month_start_spend == 0.0


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_clear_pool_team_budget_uses_remaining_duration_from_last_purchase(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC) - timedelta(days=10),
            stripe_payment_id=f"clear-pool-remaining-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 50.0, "budget_duration": "355d"}
    }

    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["max_budget"] == 50.0
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "355d"


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_team_member_budget_endpoint(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    test_team_user.role = UserRole.TEAM_ADMIN
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 1.23},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "team_member"
    assert data["team_id"] == test_team.id
    assert data["user_id"] == test_team_user.id
    assert data["max_budget"] == 1.23
    mock_update_team_member.assert_awaited_once()
    assert mock_update_team_member.await_args.kwargs["role"] == "user"
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.team_id == test_team.id,
            DBSpendCap.user_id == test_team_user.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.max_budget == 1.23
    assert cap.budget_duration == "1mo"


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_team_member_budget_returns_effective_duration(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
):
    test_team_user.role = UserRole.TEAM_ADMIN
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 2.5},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["budget_duration"] == "1mo"
    mock_update_team_member.assert_awaited_once()


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_team_member_budget_allows_cap_above_pool_purchases(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team_user.team_id = test_team.id
    db.add(test_team)
    db.add(test_team_user)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pool-member-cap-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200
    mock_update_team_member.assert_awaited_once()


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_pool_member_budget_allows_any_value_for_dedicated_team(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    """Dedicated POOL teams must allow setting a member budget above $0 purchases."""
    test_team.budget_type = BudgetType.POOL
    test_team.hide_public_regions = True
    test_team_user.team_id = test_team.id
    db.add(test_team)
    db.add(test_team_user)
    db.commit()

    mock_update_team_member.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_team_member.assert_awaited_once()
    assert mock_update_team_member.await_args.kwargs["max_budget_in_team"] == 60.0


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_pool_member_budget_allows_setting_cap_before_first_purchase(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team_user.team_id = test_team.id
    db.add(test_team)
    db.add(test_team_user)
    db.commit()

    mock_update_team_member.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_team_member.assert_awaited_once()
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.team_id == test_team.id,
            DBSpendCap.user_id == test_team_user.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.max_budget == 60.0


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_pool_member_budget_returns_configured_cap_before_first_purchase(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    test_team_user.team_id = test_team.id
    db.add(test_team)
    db.add(test_team_user)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 11.0},
    )
    assert response.status_code == 200, response.json()
    assert response.json()["max_budget"] == 11.0
    mock_update_team_member.assert_awaited_once()


@patch("app.api.spend.logger.warning")
@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_get_team_spend_logs_when_litellm_key_cannot_map_to_db_key(
    mock_get_team_info,
    mock_warning,
    client,
    team_admin_token,
    test_team,
    test_region,
):
    mock_get_team_info.return_value = {
        "team_info": {"spend": 2.0, "max_budget": 10.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": "missing-key"},
                "user_id": "999999",
                "spend": 2.0,
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    assert mock_warning.called


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_endpoint_forces_monthly_duration(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_region,
    db,
):
    key = DBPrivateAIKey(
        name="monthly-budget-key",
        litellm_token="monthly-budget-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team_user.team_id,
    )
    db.add(key)
    db.commit()
    mock_get_key_info.return_value = {
        "info": {
            "spend": 1.0,
            "expires": "2026-12-31T23:59:59Z",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "max_budget": 8.0,
            "budget_duration": "1mo",
            "budget_reset_at": "2026-06-01T00:00:00Z",
        }
    }
    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 8.0},
    )
    assert response.status_code == 200
    mock_update_key_budget.assert_awaited_once()
    assert mock_update_key_budget.await_args.kwargs["budget_duration"] == "1mo"
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.key_id == key.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.max_budget == 8.0
    assert cap.budget_duration == "1mo"


@patch("app.api.spend.invalidate_user_spend_cache")
@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_invalidates_user_spend_cache_for_team_keys(
    mock_update_key_budget,
    mock_get_key_info,
    mock_invalidate_user_spend_cache,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    member_one = DBUser(
        email="cache-member-one@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        team_id=test_team.id,
    )
    member_two = DBUser(
        email="cache-member-two@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        team_id=test_team.id,
    )
    key = DBPrivateAIKey(
        name="team-cache-key",
        litellm_token="team-cache-key-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add_all([member_one, member_two, key])
    db.commit()
    mock_get_key_info.return_value = {
        "info": {"max_budget": 8.0, "budget_duration": "1mo"}
    }

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 8.0},
    )
    assert response.status_code == 200
    invalidated_emails = {
        call.args[1] for call in mock_invalidate_user_spend_cache.call_args_list
    }
    assert "cache-member-one@example.com" in invalidated_emails
    assert "cache-member-two@example.com" in invalidated_emails


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_endpoint_clear_budget(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_region,
    db,
):
    key = DBPrivateAIKey(
        name="clear-budget-key",
        litellm_token="clear-budget-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team_user.team_id,
    )
    db.add(key)
    db.commit()
    mock_get_key_info.return_value = {
        "info": {
            "spend": 1.0,
            "expires": "2026-12-31T23:59:59Z",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-01-02T00:00:00Z",
            "max_budget": None,
            "budget_duration": None,
            "budget_reset_at": None,
        }
    }
    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": None},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "key"
    assert data["key_id"] == key.id
    assert data["max_budget"] is None
    mock_update_key_budget.assert_awaited_once()
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.key_id == key.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.max_budget is None
    assert cap.budget_duration is None


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_allows_cap_above_pool_purchases(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team_user.team_id = test_team.id
    key = DBPrivateAIKey(
        name="pool-key-cap-check",
        litellm_token="pool-key-cap-check-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team.id,
    )
    db.add(test_team)
    db.add(test_team_user)
    db.add(key)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pool-key-cap-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    mock_get_key_info.return_value = {
        "info": {"max_budget": 60.0, "budget_duration": "1mo"}
    }

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200
    mock_update_key_budget.assert_awaited_once()


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_invoice_key_budget_allows_any_value_for_dedicated_team(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    """Dedicated invoice teams allow setting key budgets without purchase gating."""
    test_team.budget_type = "periodic"
    test_team.require_purchase_for_requests = False
    test_team.hide_public_regions = True
    db.add(test_team)
    db.commit()

    key = DBPrivateAIKey(
        name="dedicated-key",
        litellm_token="dedicated-key-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {"max_budget": 50.0, "budget_duration": "1mo"}
    }
    mock_update_key_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 50.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_key_budget.assert_awaited_once()
    assert mock_update_key_budget.await_args.kwargs["max_budget"] == 50.0


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_pool_key_budget_allows_setting_cap_before_first_purchase(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    test_team_user.team_id = test_team.id
    db.add(test_team)
    db.add(test_team_user)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    key = DBPrivateAIKey(
        name="pool-key-prepurchase-cap",
        litellm_token="pool-key-prepurchase-cap-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {"max_budget": 0.0, "budget_duration": "1mo"}
    }
    mock_update_key_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_key_budget.assert_awaited_once()
    assert mock_update_key_budget.await_args.kwargs["max_budget"] == 0.0
    assert mock_update_key_budget.await_args.kwargs["clear_max_budget"] is False
    assert response.json()["max_budget"] == 60.0
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.key_id == key.id,
        )
        .first()
    )
    assert cap is not None
    assert cap.max_budget == 60.0


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_prepaid_pool_key_budget_locks_dedicated_team_before_purchase(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    test_team.hide_public_regions = True
    db.add(test_team)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    key = DBPrivateAIKey(
        name="dedicated-prepaid-key",
        litellm_token="dedicated-prepaid-key-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {"max_budget": 50.0, "budget_duration": "1mo"}
    }
    mock_update_key_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 50.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_key_budget.assert_awaited_once()
    assert mock_update_key_budget.await_args.kwargs["max_budget"] == 0.0
    assert mock_update_key_budget.await_args.kwargs["clear_max_budget"] is False


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_pool_key_budget_prepurchase_locks_only_target_key(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    test_team_user.team_id = test_team.id
    db.add(test_team)
    db.add(test_team_user)
    db.commit()
    (
        db.query(DBPoolPurchase)
        .filter(
            DBPoolPurchase.team_id == test_team.id,
            DBPoolPurchase.region_id == test_region.id,
        )
        .delete()
    )
    db.commit()

    key1 = DBPrivateAIKey(
        name="pool-key-target",
        litellm_token="pool-key-target-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team.id,
    )
    key2 = DBPrivateAIKey(
        name="pool-key-other",
        litellm_token="pool-key-other-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team.id,
    )
    db.add(key1)
    db.add(key2)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {"max_budget": 0.0, "budget_duration": "1mo"}
    }
    mock_update_key_budget.return_value = None

    response = client.put(
        f"/spend/{test_region.id}/key/{key1.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0},
    )
    assert response.status_code == 200, response.json()
    mock_update_key_budget.assert_awaited_once()
    called_tokens = [
        call.kwargs["litellm_token"] for call in mock_update_key_budget.await_args_list
    ]
    assert called_tokens == [key1.litellm_token]


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_clear_key_budget_endpoint(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_region,
    db,
):
    key = DBPrivateAIKey(
        name="clear-key-endpoint",
        litellm_token="clear-key-endpoint-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team_user.team_id,
    )
    db.add(key)
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team_user.team_id,
            user_id=test_team_user.id,
            key_id=key.id,
            max_budget=10.0,
            budget_duration="1mo",
        )
    )
    db.commit()
    mock_get_key_info.return_value = {
        "info": {"max_budget": None, "budget_duration": None}
    }

    response = client.post(
        f"/spend/{test_region.id}/key/{key.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_key_budget.assert_awaited_once_with(
        litellm_token=key.litellm_token,
        budget_duration=None,
        max_budget=None,
        clear_max_budget=True,
        clear_budget_duration=True,
    )
    assert response.json()["max_budget"] is None
    assert response.json()["budget_duration"] is None
    cap = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "key",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.key_id == key.id,
        )
        .first()
    )
    assert cap is None


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_clear_team_member_budget_endpoint(
    mock_update_team_member, client, admin_token, test_team, test_team_user, test_region
):
    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_member.assert_awaited_once()
    assert mock_update_team_member.await_args.kwargs["max_budget_in_team"] is None


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_clear_team_member_budget_endpoint_deletes_spend_cap(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    cap = DBSpendCap(
        scope="team_member",
        region_id=test_region.id,
        team_id=test_team.id,
        user_id=test_team_user.id,
        max_budget=3.0,
        budget_duration="1mo",
    )
    db.add(cap)
    db.commit()

    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/member/{test_team_user.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_member.assert_awaited_once()
    remaining = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team_member",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.team_id == test_team.id,
            DBSpendCap.user_id == test_team_user.id,
        )
        .first()
    )
    assert remaining is None


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_clear_team_budget_endpoint_periodic(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
):
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 27.0, "budget_duration": "1mo"}
    }

    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "1mo"


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_clear_team_budget_endpoint_deletes_team_spend_cap(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    db.add(
        DBSpendCap(
            scope="team",
            region_id=test_region.id,
            team_id=test_team.id,
            max_budget=9.0,
            budget_duration="1mo",
        )
    )
    db.commit()
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 27.0, "budget_duration": "1mo"}
    }

    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()
    remaining = (
        db.query(DBSpendCap)
        .filter(
            DBSpendCap.scope == "team",
            DBSpendCap.region_id == test_region.id,
            DBSpendCap.team_id == test_team.id,
        )
        .first()
    )
    assert remaining is None


@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_forbidden_for_key_creator(
    mock_update_team_budget,
    client,
    team_key_creator_token,
    test_team,
    test_region,
):
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {team_key_creator_token}"},
        json={"max_budget": 12.5},
    )
    assert response.status_code == 403
    mock_update_team_budget.assert_not_awaited()


@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_rejects_unassociated_dedicated_region(
    mock_update_team_budget,
    client,
    admin_token,
    test_team,
    db,
):
    dedicated = DBRegion(
        name="dedicated-spend",
        label="Dedicated Spend",
        description="Dedicated region for spend authorization tests",
        postgres_host="dedicated-postgres",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="https://dedicated-litellm.test",
        litellm_api_key="dedicated-litellm-key",
        is_active=True,
        is_dedicated=True,
    )
    db.add(dedicated)
    db.commit()
    db.refresh(dedicated)

    response = client.put(
        f"/spend/{dedicated.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 5.0},
    )
    assert response.status_code == 400
    assert "not associated with this region" in response.json()["detail"]
    mock_update_team_budget.assert_not_awaited()


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_owner_only_key_path(
    mock_update_key_budget,
    mock_get_key_info,
    client,
    admin_token,
    test_team_user,
    test_region,
    db,
):
    key = DBPrivateAIKey(
        name="owner-only-key",
        litellm_token="owner-only-key-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=None,
    )
    db.add(key)
    db.commit()
    mock_get_key_info.return_value = {
        "info": {"max_budget": 2.0, "budget_duration": "1mo"}
    }

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 2.0},
    )
    assert response.status_code == 200
    mock_update_key_budget.assert_awaited_once()
    assert mock_update_key_budget.await_args.kwargs["budget_duration"] == "1mo"


@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_forbidden_for_cross_team_admin(
    mock_update_key_budget,
    client,
    team_admin_token,
    test_region,
    db,
):
    other_team = DBTeam(name="other-team")
    db.add(other_team)
    db.commit()
    db.refresh(other_team)

    other_admin = DBUser(
        email="other-team-admin@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        is_admin=False,
        role="admin",
        team_id=other_team.id,
        created_at=datetime.now(UTC),
    )
    db.add(other_admin)
    db.commit()

    key = DBPrivateAIKey(
        name="other-team-key",
        litellm_token="other-team-key-token",
        region_id=test_region.id,
        team_id=other_team.id,
    )
    db.add(key)
    db.add(
        DBTeamRegion(
            team_id=other_team.id,
            region_id=test_region.id,
        )
    )
    db.commit()

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {team_admin_token}"},
        json={"max_budget": 2.0},
    )
    assert response.status_code == 403
    mock_update_key_budget.assert_not_awaited()


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_clear_team_budget_endpoint_pool_restores_purchases(
    mock_update_team_budget,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = "pool"
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"clear-pool-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 50.0, "budget_duration": "365d"}
    }

    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["max_budget"] == 50.0


def test_spend_caps_unique_team_scope_enforced(db, test_region, test_team):
    first = DBSpendCap(scope="team", region_id=test_region.id, team_id=test_team.id)
    db.add(first)
    db.commit()

    duplicate = DBSpendCap(scope="team", region_id=test_region.id, team_id=test_team.id)
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_spend_caps_unique_team_member_scope_enforced(
    db, test_region, test_team, test_team_user
):
    first = DBSpendCap(
        scope="team_member",
        region_id=test_region.id,
        team_id=test_team.id,
        user_id=test_team_user.id,
    )
    db.add(first)
    db.commit()

    duplicate = DBSpendCap(
        scope="team_member",
        region_id=test_region.id,
        team_id=test_team.id,
        user_id=test_team_user.id,
    )
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


def test_spend_caps_unique_key_scope_enforced(db, test_region, test_team_user):
    key = DBPrivateAIKey(
        name="unique-key-cap",
        litellm_token="unique-key-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
    )
    db.add(key)
    db.commit()
    db.refresh(key)

    first = DBSpendCap(scope="key", region_id=test_region.id, key_id=key.id)
    db.add(first)
    db.commit()

    duplicate = DBSpendCap(scope="key", region_id=test_region.id, key_id=key.id)
    db.add(duplicate)
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_get_team_spend_uses_db_key_cap_regardless_of_team_type(
    mock_get_team_info, client, admin_token, test_team, test_region, db
):
    """DB key caps must override LiteLLM per-key max_budget for any team type/purchase state."""
    # Use a periodic (non-POOL) team — no purchase needed
    test_team.budget_type = "periodic"
    test_team.require_purchase_for_requests = False
    db.add(test_team)
    db.commit()

    key = DBPrivateAIKey(
        name="periodic-team-key",
        litellm_token="periodic-team-key-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team.id,
            key_id=key.id,
            max_budget=15.0,
            budget_duration="1mo",
        )
    )
    db.commit()

    # LiteLLM reports a different max_budget — the DB cap must win
    mock_get_team_info.return_value = {
        "team_info": {"spend": 1.0, "max_budget": 100.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key.name},
                "spend": 1.0,
                "max_budget": 99.0,
                "user_id": None,
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["keys"][0]["max_budget"] == 15.0


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_get_team_spend_uses_db_key_cap_for_pool_team_after_purchase(
    mock_get_team_info, client, admin_token, test_team, test_region, db
):
    """DB key caps must override LiteLLM per-key max_budget for POOL teams that have made a purchase."""
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pool-post-purchase-key-cap-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    key = DBPrivateAIKey(
        name="pool-post-purchase-key",
        litellm_token="pool-post-purchase-key-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team.id,
            key_id=key.id,
            max_budget=20.0,
            budget_duration="1mo",
        )
    )
    db.commit()

    # LiteLLM reports a different value — DB cap must still win after purchase
    mock_get_team_info.return_value = {
        "team_info": {"spend": 2.0, "max_budget": 50.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key.name},
                "spend": 2.0,
                "max_budget": 50.0,
                "user_id": None,
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["keys"][0]["max_budget"] == 20.0


@patch("app.api.spend.LiteLLMService.get_user_info", new_callable=AsyncMock)
def test_get_user_spend_db_key_cap_beats_member_cap_beats_litellm_for_periodic_team(
    mock_get_user_info, client, admin_token, test_team, test_region, db
):
    """For a periodic team, DB key cap > DB member cap > LiteLLM-reported value."""
    test_team.budget_type = "periodic"
    test_team.require_purchase_for_requests = False
    db.add(test_team)
    db.commit()

    user = DBUser(
        email="periodic-cap-user@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        team_id=test_team.id,
        role=UserRole.TEAM_ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    key_with_key_cap = DBPrivateAIKey(
        name="periodic-user-key-cap",
        litellm_token="periodic-user-key-cap-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    key_with_member_cap = DBPrivateAIKey(
        name="periodic-user-member-cap",
        litellm_token="periodic-user-member-cap-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    key_with_litellm_only = DBPrivateAIKey(
        name="periodic-user-litellm-only",
        litellm_token="periodic-user-litellm-only-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    db.add_all([key_with_key_cap, key_with_member_cap, key_with_litellm_only])
    db.commit()

    db.add_all(
        [
            DBSpendCap(
                scope="team_member",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user.id,
                max_budget=5.0,
                budget_duration="1mo",
            ),
            DBSpendCap(
                scope="key",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user.id,
                key_id=key_with_key_cap.id,
                max_budget=9.0,
                budget_duration="1mo",
            ),
        ]
    )
    db.commit()

    # LiteLLM reports values that should all be overridden by DB caps
    mock_get_user_info.return_value = {
        "user_info": {"spend": 0.5},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key_with_key_cap.name},
                "user_id": str(user.id),
                "spend": 0.1,
                "max_budget": 99.0,
            },
            {
                "metadata": {"amazeeai_private_ai_key_name": key_with_member_cap.name},
                "user_id": str(user.id),
                "spend": 0.2,
                "max_budget": 99.0,
            },
            {
                "metadata": {
                    "amazeeai_private_ai_key_name": key_with_litellm_only.name
                },
                "user_id": str(user.id),
                "spend": 0.2,
                "max_budget": 99.0,
            },
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/user/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    max_budget_by_name = {k["key_name"]: k["max_budget"] for k in data["keys"]}
    # DB key cap wins over member cap and LiteLLM
    assert max_budget_by_name[key_with_key_cap.name] == 9.0
    # DB member cap wins over LiteLLM when no key cap is present
    assert max_budget_by_name[key_with_member_cap.name] == 5.0
    # DB member cap also applied to key that has no explicit key cap
    assert max_budget_by_name[key_with_litellm_only.name] == 5.0


@patch("app.api.spend.LiteLLMService.get_user_info", new_callable=AsyncMock)
def test_get_user_spend_db_key_cap_beats_member_cap_beats_litellm_for_purchased_pool_team(
    mock_get_user_info, client, admin_token, test_team, test_region, db
):
    """For a POOL team with a purchase, DB key cap > DB member cap > LiteLLM-reported value."""
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=10000,
            currency="USD",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"user-spend-pool-cap-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    user = DBUser(
        email="pool-purchased-cap-user@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        team_id=test_team.id,
        role=UserRole.TEAM_ADMIN,
    )
    db.add(user)
    db.commit()
    db.refresh(user)

    key_with_key_cap = DBPrivateAIKey(
        name="pool-purchased-key-cap",
        litellm_token="pool-purchased-key-cap-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    key_with_member_cap = DBPrivateAIKey(
        name="pool-purchased-member-cap",
        litellm_token="pool-purchased-member-cap-token",
        region_id=test_region.id,
        owner_id=user.id,
        team_id=test_team.id,
    )
    db.add_all([key_with_key_cap, key_with_member_cap])
    db.commit()

    db.add_all(
        [
            DBSpendCap(
                scope="team_member",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user.id,
                max_budget=8.0,
                budget_duration="1mo",
            ),
            DBSpendCap(
                scope="key",
                region_id=test_region.id,
                team_id=test_team.id,
                user_id=user.id,
                key_id=key_with_key_cap.id,
                max_budget=12.0,
                budget_duration="1mo",
            ),
        ]
    )
    db.commit()

    # LiteLLM reports values that should be overridden by DB caps
    mock_get_user_info.return_value = {
        "user_info": {"spend": 1.0},
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key_with_key_cap.name},
                "user_id": str(user.id),
                "spend": 0.5,
                "max_budget": 50.0,
            },
            {
                "metadata": {"amazeeai_private_ai_key_name": key_with_member_cap.name},
                "user_id": str(user.id),
                "spend": 0.5,
                "max_budget": 50.0,
            },
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/user/{user.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    max_budget_by_name = {k["key_name"]: k["max_budget"] for k in data["keys"]}
    # DB key cap wins over both member cap and LiteLLM after purchase
    assert max_budget_by_name[key_with_key_cap.name] == 12.0
    # DB member cap wins over LiteLLM after purchase
    assert max_budget_by_name[key_with_member_cap.name] == 8.0


# ── _compute_period_start unit tests ─────────────────────────────────


def test_compute_period_start_31d():
    from app.api.spend import _compute_period_start

    reset_at = datetime(2026, 6, 8, 0, 0, 0, tzinfo=UTC)
    result = _compute_period_start(reset_at, "31d")
    assert result == datetime(2026, 5, 8, 0, 0, 0, tzinfo=UTC)


def test_compute_period_start_365d():
    from app.api.spend import _compute_period_start

    reset_at = datetime(2027, 5, 8, 0, 0, 0, tzinfo=UTC)
    result = _compute_period_start(reset_at, "365d")
    assert result == datetime(2026, 5, 8, 0, 0, 0, tzinfo=UTC)


def test_compute_period_start_1mo_resets_on_first():
    """1mo resets on 1st of next month → period_start = 1st of current month."""
    from app.api.spend import _compute_period_start

    reset_at = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    result = _compute_period_start(reset_at, "1mo")
    assert result == datetime(2026, 5, 1, 0, 0, 0, tzinfo=UTC)


def test_compute_period_start_1mo_january_wraps():
    """1mo resetting on Feb 1st → period starts Jan 1st."""
    from app.api.spend import _compute_period_start

    reset_at = datetime(2027, 2, 1, 0, 0, 0, tzinfo=UTC)
    result = _compute_period_start(reset_at, "1mo")
    assert result == datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)


def test_compute_period_start_1mo_december_wraps_year():
    """1mo resetting on Jan 1st → period starts Dec 1st of prev year."""
    from app.api.spend import _compute_period_start

    reset_at = datetime(2027, 1, 1, 0, 0, 0, tzinfo=UTC)
    result = _compute_period_start(reset_at, "1mo")
    assert result == datetime(2026, 12, 1, 0, 0, 0, tzinfo=UTC)


def test_compute_period_start_none_inputs():
    from app.api.spend import _compute_period_start

    assert _compute_period_start(None, "31d") is None
    assert _compute_period_start(datetime(2026, 6, 1, tzinfo=UTC), None) is None
    assert _compute_period_start(None, None) is None
    assert _compute_period_start(datetime(2026, 6, 1, tzinfo=UTC), "") is None


def test_compute_period_start_unknown_duration():
    from app.api.spend import _compute_period_start

    reset_at = datetime(2026, 6, 1, 0, 0, 0, tzinfo=UTC)
    assert _compute_period_start(reset_at, "2w") is None


# ── Spend endpoint period fields tests ───────────────────────────────


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_team_spend_includes_period_fields_for_periodic_team(
    mock_get_team_info, client, admin_token, test_team, test_region, db
):
    key = DBPrivateAIKey(
        name="periodic-period-key",
        litellm_token="periodic-period-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {
            "spend": 5.0,
            "max_budget": 10.0,
            "budget_duration": "31d",
            "budget_reset_at": "2026-06-08T00:00:00Z",
        },
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key.name},
                "user_id": None,
                "spend": 5.0,
                "max_budget": 10.0,
                "budget_duration": "31d",
                "budget_reset_at": "2026-06-08T00:00:00Z",
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    # Team-level period fields
    assert data["budget_duration"] == "31d"
    assert data["budget_reset_at"] == "2026-06-08T00:00:00Z"
    assert data["period_start"] == "2026-05-08T00:00:00Z"
    # Per-key period fields
    assert data["keys"][0]["budget_duration"] == "31d"
    assert data["keys"][0]["budget_reset_at"] == "2026-06-08T00:00:00Z"
    assert data["keys"][0]["period_start"] == "2026-05-08T00:00:00Z"


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_team_spend_period_fields_null_when_no_budget(
    mock_get_team_info, client, admin_token, test_team, test_region, db
):
    """When LiteLLM has no budget set, period fields should be null."""
    key = DBPrivateAIKey(
        name="no-budget-key",
        litellm_token="no-budget-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {
            "spend": 1.0,
            "max_budget": None,
            "budget_duration": None,
            "budget_reset_at": None,
        },
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key.name},
                "user_id": None,
                "spend": 1.0,
                "max_budget": None,
                "budget_duration": None,
                "budget_reset_at": None,
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["budget_duration"] is None
    assert data["budget_reset_at"] is None
    assert data["period_start"] is None
    assert data["keys"][0]["budget_duration"] is None
    assert data["keys"][0]["budget_reset_at"] is None
    assert data["keys"][0]["period_start"] is None


@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
def test_key_spend_includes_period_start(
    mock_get_key_info, client, admin_token, test_team, test_team_user, test_region, db
):
    key = DBPrivateAIKey(
        name="period-key",
        litellm_token="period-key-token",
        region_id=test_region.id,
        owner_id=test_team_user.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_get_key_info.return_value = {
        "info": {
            "spend": 3.0,
            "expires": "2026-12-31T23:59:59Z",
            "created_at": "2026-01-01T00:00:00Z",
            "updated_at": "2026-05-01T00:00:00Z",
            "max_budget": 10.0,
            "budget_duration": "31d",
            "budget_reset_at": "2026-06-08T00:00:00Z",
        }
    }

    response = client.get(
        f"/spend/{test_region.id}/key/{key.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["budget_duration"] == "31d"
    assert data["budget_reset_at"] == "2026-06-08T00:00:00Z"
    assert data["period_start"] == "2026-05-08T00:00:00Z"


@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
def test_pool_key_with_cap_shows_period_fields(
    mock_get_team_info, client, admin_token, test_team, test_region, db
):
    """POOL team key with a spend cap gets 1mo budget_duration from
    update_key_budget, so period fields should be populated."""
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()

    key = DBPrivateAIKey(
        name="pool-cap-key",
        litellm_token="pool-cap-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team.id,
            key_id=key.id,
            max_budget=5.0,
            budget_duration="1mo",
        )
    )
    db.commit()

    mock_get_team_info.return_value = {
        "team_info": {
            "spend": 0.5,
            "max_budget": 20.0,
            "budget_duration": "365d",
            "budget_reset_at": "2027-05-08T00:00:00Z",
        },
        "keys": [
            {
                "metadata": {"amazeeai_private_ai_key_name": key.name},
                "user_id": None,
                "spend": 0.5,
                "max_budget": 5.0,
                "budget_duration": "1mo",
                "budget_reset_at": "2026-06-01T00:00:00Z",
            }
        ],
    }

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    # Team has 365d from purchase
    assert data["budget_duration"] == "365d"
    assert data["budget_reset_at"] == "2027-05-08T00:00:00Z"
    assert data["period_start"] == "2026-05-08T00:00:00Z"
    # Key has 1mo cap
    k = data["keys"][0]
    assert k["budget_duration"] == "1mo"
    assert k["budget_reset_at"] == "2026-06-01T00:00:00Z"
    assert k["period_start"] == "2026-05-01T00:00:00Z"


# ---------------------------------------------------------------------------
# /spend/{region_id}/team/{team_id}/history tests
# ---------------------------------------------------------------------------


def test_get_team_spend_history_returns_empty_for_no_periods(
    client, team_admin_token, test_team, test_region
):
    """Endpoint returns an empty periods list when no snapshots exist."""
    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert data["periods"] == []


def test_get_team_spend_history_returns_periods_ordered_desc(
    client, team_admin_token, test_team, test_region, db
):
    """Periods are returned newest-first (period_end desc)."""
    from datetime import UTC, datetime
    from app.db.models import DBTeamSpendPeriod

    p1 = DBTeamSpendPeriod(
        team_id=test_team.id,
        region_id=test_region.id,
        budget_type="periodic",
        period_start=datetime(2026, 3, 1, tzinfo=UTC),
        period_end=datetime(2026, 4, 1, tzinfo=UTC),
        total_spend=5.0,
        source="test",
    )
    p2 = DBTeamSpendPeriod(
        team_id=test_team.id,
        region_id=test_region.id,
        budget_type="periodic",
        period_start=datetime(2026, 4, 1, tzinfo=UTC),
        period_end=datetime(2026, 5, 1, tzinfo=UTC),
        total_spend=10.0,
        source="test",
    )
    db.add_all([p1, p2])
    db.commit()

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["periods"]) == 2
    # Newest period (April→May) must come first
    assert data["periods"][0]["total_spend"] == 10.0
    assert data["periods"][1]["total_spend"] == 5.0


def test_get_team_spend_history_includes_key_rows(
    client, team_admin_token, test_team, test_region, test_team_user, db
):
    """Key-level spend rows are embedded inside each period."""
    from datetime import UTC, datetime
    from app.db.models import DBTeamSpendPeriod, DBTeamSpendPeriodKey, DBPrivateAIKey

    key = DBPrivateAIKey(
        name="hist-key",
        litellm_token="hist-token",
        region_id=test_region.id,
        team_id=test_team.id,
        owner_id=test_team_user.id,
    )
    db.add(key)
    db.commit()

    period = DBTeamSpendPeriod(
        team_id=test_team.id,
        region_id=test_region.id,
        budget_type="periodic",
        period_start=datetime(2026, 4, 1, tzinfo=UTC),
        period_end=datetime(2026, 5, 1, tzinfo=UTC),
        total_spend=7.5,
        source="test",
    )
    db.add(period)
    db.flush()

    db.add(
        DBTeamSpendPeriodKey(
            team_spend_period_id=period.id,
            key_id=key.id,
            owner_id=test_team_user.id,
            key_name_snapshot="hist-key",
            spend=7.5,
            max_budget=50.0,
            prompt_tokens=100,
            completion_tokens=50,
            total_tokens=150,
        )
    )
    db.commit()

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["periods"]) == 1
    keys = data["periods"][0]["keys"]
    assert len(keys) == 1
    assert keys[0]["key_id"] == key.id
    assert keys[0]["owner_id"] == test_team_user.id
    assert keys[0]["key_name_snapshot"] == "hist-key"
    assert keys[0]["spend"] == 7.5
    assert keys[0]["max_budget"] == 50.0


def test_get_team_spend_history_access_denied_for_other_team(
    client, team_admin_token, test_region, db
):
    """Team admin cannot access history of a different team."""
    from app.db.models import DBTeam

    other_team = DBTeam(
        name="Other Team",
        admin_email="other@example.com",
        is_active=True,
    )
    db.add(other_team)
    db.commit()

    response = client.get(
        f"/spend/{test_region.id}/team/{other_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code in (403, 404)


def test_get_team_spend_history_404_for_nonexistent_team(
    client, admin_token, test_region
):
    """Returns 404 when the requested team does not exist."""
    response = client.get(
        f"/spend/{test_region.id}/team/999999/history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 404


def test_get_team_spend_history_admin_can_access_any_team(
    client, admin_token, test_team, test_region
):
    """Site admins can access history for any team."""
    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200


def test_get_team_spend_history_includes_periodic_transactions(
    client, team_admin_token, test_team, test_region, db
):
    from datetime import datetime, UTC
    from app.db.models import DBPeriodicPayment, DBPeriodicBudgetLedgerEntry

    test_team.budget_type = "periodic"
    db.add(test_team)
    db.flush()

    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="cs_hist_periodic_1",
        amount_cents=500,
        currency="usd",
        payment_type="topup",
        status="completed",
        sync_status="success",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.flush()
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=payment.id,
            stripe_payment_id="cs_hist_periodic_1",
            amount_cents=500,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            is_active=True,
        )
    )
    db.commit()

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data["periodic_transactions"]) >= 1
    assert data["periodic_transactions"][0]["payment_type"] in ("subscription", "topup")


def test_get_team_spend_history_periodic_transactions_empty_for_non_periodic_team(
    client, team_admin_token, test_team, test_region, db
):
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["periodic_transactions"] == []


def test_get_team_spend_history_periodic_transactions_region_scoped(
    client, team_admin_token, test_team, test_region, db
):
    from datetime import datetime, UTC
    from app.db.models import DBPeriodicPayment, DBPeriodicBudgetLedgerEntry, DBRegion

    test_team.budget_type = "periodic"
    db.add(test_team)
    db.flush()
    second_region = DBRegion(
        name=f"hist-second-region-{test_team.id}",
        postgres_host="localhost",
        postgres_port=5432,
        postgres_admin_user="postgres",
        postgres_admin_password="postgres",
        litellm_api_url="https://hist-second-region.example.com",
        litellm_api_key="hist-second-region-key",
        is_active=True,
    )
    db.add(second_region)
    db.flush()

    p1 = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="cs_hist_region_1",
        amount_cents=500,
        currency="usd",
        payment_type="topup",
        status="completed",
        sync_status="success",
        payment_date=datetime.now(UTC),
    )
    p2 = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="cs_hist_region_2",
        amount_cents=700,
        currency="usd",
        payment_type="topup",
        status="completed",
        sync_status="success",
        payment_date=datetime.now(UTC),
    )
    db.add_all([p1, p2])
    db.flush()
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=p1.id,
            stripe_payment_id="cs_hist_region_1",
            amount_cents=500,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            is_active=True,
        )
    )
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=second_region.id,
            entry_type="topup",
            source_payment_id=p2.id,
            stripe_payment_id="cs_hist_region_2",
            amount_cents=700,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            is_active=True,
        )
    )
    db.commit()

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}/history",
        headers={"Authorization": f"Bearer {team_admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    ids = [t["stripe_payment_id"] for t in data["periodic_transactions"]]
    assert "cs_hist_region_1" in ids
    assert "cs_hist_region_2" not in ids


@pytest.mark.parametrize(
    "sub_amount,sub_consumed,topup_amount,topup_consumed,total_spend,expected_remaining",
    [
        # Fresh cycle: no spend, no consumption → remaining = full budget
        (1000, 0, 500, 0, 0.0, 1500),
        # Mid-cycle: spend tracked by LiteLLM only, consumed_cents still 0
        (1000, 0, 500, 0, 3.0, 1200),
        # After cycle-close: consumed_cents updated, LiteLLM spend reset to 0
        (1000, 300, 500, 0, 0.0, 1200),
        # Both consumed and LiteLLM spend present (should NOT happen in prod
        # but verifies the invariant formula handles it by clamping to 0)
        (1000, 300, 500, 100, 5.0, 600),
        # Edge: spend exceeds purchased → clamped to 0
        (1000, 0, 0, 0, 20.0, 0),
        # No topups, partial consumption after cycle-close
        (2000, 500, 0, 0, 0.0, 1500),
    ],
)
@patch("app.api.spend.LiteLLMService.get_team_info", new_callable=AsyncMock)
@patch("app.api.spend.LiteLLMService.get_key_info", new_callable=AsyncMock)
def test_periodic_live_remaining_invariant(
    mock_get_key_info,
    mock_get_team_info,
    client,
    admin_token,
    test_team,
    test_region,
    db,
    sub_amount,
    sub_consumed,
    topup_amount,
    topup_consumed,
    total_spend,
    expected_remaining,
):
    """Assert live_remaining matches purchased - total_spend across cycle states.

    The formula is: remaining = (sub_remaining + topup_remaining) - total_spend
    where sub_remaining = sub_amount - sub_consumed, topup_remaining = topup_amount - topup_consumed.
    This is correct because consumed_cents and total_spend are never both
    non-zero for the same dollars — consumed_cents is only incremented at
    cycle close when total_spend is simultaneously reset.
    """
    from app.db.models import DBPeriodicBudgetLedgerEntry, DBPeriodicPayment

    # Create payment records for ledger entries
    sub_payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id="inv_sub_invariant",
        amount_cents=sub_amount,
        currency="usd",
        payment_type="subscription",
        status="completed",
        sync_status="success",
        payment_date=datetime.now(UTC),
    )
    db.add(sub_payment)
    db.flush()

    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="subscription",
            source_payment_id=sub_payment.id,
            stripe_payment_id="inv_sub_invariant",
            amount_cents=sub_amount,
            consumed_cents=sub_consumed,
            purchased_at=datetime.now(UTC) - timedelta(days=15),
            expires_at=datetime.now(UTC) + timedelta(days=16),
            is_active=True,
        )
    )

    if topup_amount > 0:
        topup_payment = DBPeriodicPayment(
            team_id=test_team.id,
            stripe_payment_id="inv_topup_invariant",
            amount_cents=topup_amount,
            currency="usd",
            payment_type="topup",
            status="completed",
            sync_status="success",
            payment_date=datetime.now(UTC),
        )
        db.add(topup_payment)
        db.flush()

        db.add(
            DBPeriodicBudgetLedgerEntry(
                team_id=test_team.id,
                region_id=test_region.id,
                entry_type="topup",
                source_payment_id=topup_payment.id,
                stripe_payment_id="inv_topup_invariant",
                amount_cents=topup_amount,
                consumed_cents=topup_consumed,
                purchased_at=datetime.now(UTC) - timedelta(days=10),
                expires_at=datetime.now(UTC) + timedelta(days=20),
                is_active=True,
            )
        )

    # Ensure team has a key in the region so the endpoint returns data
    existing_key = (
        db.query(DBPrivateAIKey)
        .filter(
            DBPrivateAIKey.team_id == test_team.id,
            DBPrivateAIKey.region_id == test_region.id,
        )
        .first()
    )
    if not existing_key:
        db.add(
            DBPrivateAIKey(
                name="invariant-test-key",
                litellm_token="invariant-test-token",
                region_id=test_region.id,
                team_id=test_team.id,
            )
        )
    db.commit()

    # Mock LiteLLM team info to return total_spend
    purchased_cents = (sub_amount - sub_consumed) + (topup_amount - topup_consumed)
    purchased_dollars = purchased_cents / 100.0
    mock_get_team_info.return_value = {
        "team_info": {
            "spend": total_spend,
            "max_budget": purchased_dollars,
            "budget_duration": "31d",
        },
        "keys": [],
    }
    mock_get_key_info.return_value = []

    response = client.get(
        f"/spend/{test_region.id}/team/{test_team.id}",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["periodic_budget"]["remaining_budget_cents"] == expected_remaining
