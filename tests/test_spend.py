from unittest.mock import AsyncMock, patch

from app.core.roles import UserRole
from datetime import UTC, datetime

from app.db.models import DBPoolPurchase, DBPrivateAIKey


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
            "budget_duration": "30d",
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
):
    mock_get_team_info.return_value = {
        "team_info": {"max_budget": 12.5, "budget_duration": "30d"}
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
    assert data["budget_duration"] == "30d"
    mock_update_team_budget.assert_awaited_once()


@patch("app.api.spend.LiteLLMService.update_team_member", new_callable=AsyncMock)
def test_update_team_member_budget_endpoint(
    mock_update_team_member, client, admin_token, test_team, test_team_user, test_region
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
    db.commit()
    mock_get_key_info.return_value = {
        "info": {"max_budget": None, "budget_duration": "30d"}
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
        "team_info": {"max_budget": 27.0, "budget_duration": "30d"}
    }

    response = client.post(
        f"/spend/{test_region.id}/team/{test_team.id}/budget/clear",
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 200
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] is None


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
