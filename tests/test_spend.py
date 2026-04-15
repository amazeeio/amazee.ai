from unittest.mock import AsyncMock, patch

from app.core.roles import UserRole
from datetime import UTC, datetime, timedelta

from app.core.security import get_password_hash
from app.db.models import (
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
    assert data["max_budget"] == 100.0
    assert data["prompt_tokens"] == 1200
    assert data["completion_tokens"] == 300
    assert data["total_tokens"] == 1500


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
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 12.5, "budget_duration": "1mo"}
    }
    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 12.5, "budget_duration": "30d"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["scope"] == "team"
    assert data["team_id"] == test_team.id
    assert data["max_budget"] == 12.5
    assert data["budget_duration"] == "1mo"
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "1mo"
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
    assert cap.max_budget == 12.5
    assert cap.budget_duration == "1mo"


@patch("app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock)
def test_update_team_budget_rejects_cap_above_pool_purchases(
    mock_update_team_budget,
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
            stripe_payment_id=f"pool-team-cap-{test_team.id}-{test_region.id}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    response = client.put(
        f"/spend/{test_region.id}/team/{test_team.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0, "budget_duration": "30d"},
    )
    assert response.status_code == 400
    assert "cannot exceed purchased pool budget" in response.json()["detail"]
    mock_update_team_budget.assert_not_awaited()


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
    test_team.budget_type = "pool"
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
        json={"max_budget": 12.5, "budget_duration": "1mo"},
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
    test_team.budget_type = "pool"
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
        json={"max_budget": 2.5, "budget_duration": "30d"},
    )
    assert response.status_code == 200
    data = response.json()
    assert data["budget_duration"] == "1mo"
    mock_update_team_member.assert_awaited_once()


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_team_member_budget_rejects_cap_above_pool_purchases(
    mock_update_team_member,
    client,
    admin_token,
    test_team,
    test_team_user,
    test_region,
    db,
):
    test_team.budget_type = "pool"
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
    assert response.status_code == 400
    assert "cannot exceed purchased pool budget" in response.json()["detail"]
    mock_update_team_member.assert_not_awaited()


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
        json={"max_budget": 8.0, "budget_duration": "30d"},
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
        json={"max_budget": None, "budget_duration": None},
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


@patch("app.api.spend.LiteLLMService.update_key_budget", new_callable=AsyncMock)
def test_update_key_budget_rejects_cap_above_pool_purchases(
    mock_update_key_budget,
    client,
    admin_token,
    test_team_user,
    test_team,
    test_region,
    db,
):
    test_team.budget_type = "pool"
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

    response = client.put(
        f"/spend/{test_region.id}/key/{key.id}/budget",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={"max_budget": 60.0, "budget_duration": "30d"},
    )
    assert response.status_code == 400
    assert "cannot exceed purchased pool budget" in response.json()["detail"]
    mock_update_key_budget.assert_not_awaited()


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
        "info": {"max_budget": None, "budget_duration": "1mo"}
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
    )
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
        json={"max_budget": 12.5, "budget_duration": "1mo"},
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
        json={"max_budget": 5.0, "budget_duration": "1mo"},
    )
    assert response.status_code == 400
    assert "not associated with this dedicated region" in response.json()["detail"]
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
        json={"max_budget": 2.0, "budget_duration": "30d"},
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
        json={"max_budget": 2.0, "budget_duration": "1mo"},
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
