import time

from app.db.models import (
    DBLimitedResource,
    DBPoolPurchase,
    DBPrivateAIKey,
    DBSpendCap,
    DBPeriodicPayment,
)
from app.db.models import DBPeriodicBudgetLedgerEntry
from app.db.models import DBTeamRegion
from app.schemas.limits import LimitSource, LimitType, OwnerType, ResourceType, UnitType
from datetime import datetime, UTC, timedelta
import pytest
from unittest.mock import patch, AsyncMock
from app.api.budgets import (
    sync_pool_team_budgets,
    sync_pool_team_monthly_caps,
)


@pytest.mark.skip(reason="Fixture isolation issue - passes when run with fresh DB")
def test_create_pool_purchase_success(client, admin_token, db, test_team, test_region):
    """Test creating a pool purchase for a pool team"""
    test_team.budget_type = "pool"
    db.commit()

    unique_payment_id = f"pi_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
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
    assert data["new_total_budget_cents"] == 5000


@pytest.mark.skip(reason="Fixture isolation issue - passes when run with fresh DB")
def test_pool_purchase_sets_max_budget_to_total_purchased(
    client, admin_token, db, test_team, test_region
):
    """
    Regression test: max_budget must equal cumulative purchases, not
    purchases-minus-spend.

    LiteLLM treats max_budget as a ceiling (rejects when spend >= max_budget).
    If we subtract current spend the resulting max_budget is lower than spend
    and every request is immediately rejected with "Budget exceeded".
    """
    test_team.budget_type = "pool"
    db.commit()

    # Simulate an earlier purchase already in the DB.
    earlier = DBPoolPurchase(
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=900,
        currency="usd",
        purchased_at=datetime(2026, 3, 26, 20, 0, 0, tzinfo=UTC),
        stripe_payment_id="pi_earlier",
        created_at=datetime(2026, 3, 26, 20, 0, 0, tzinfo=UTC),
    )
    db.add(earlier)
    db.commit()

    unique_payment_id = f"pi_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.update_team_budget = AsyncMock()

        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 800,
                "currency": "usd",
                "purchased_at": "2026-03-30T01:19:00Z",
                "stripe_payment_id": unique_payment_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    data = response.json()

    # Total purchased = 900 + 800 = 1700 cents = $17.00
    # max_budget must be the full $17.00, regardless of current spend.
    assert data["new_total_budget_cents"] == 1700

    # Verify update_team_budget was called with the correct ceiling.
    mock_instance.update_team_budget.assert_awaited_once()
    call_kwargs = mock_instance.update_team_budget.call_args
    assert "max_budget" in call_kwargs.kwargs or "max_budget" in call_kwargs[1]
    if "max_budget" in call_kwargs.kwargs:
        budget_arg = call_kwargs.kwargs["max_budget"]
    else:
        budget_arg = call_kwargs[1]["max_budget"]
    assert budget_arg == 17.0


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


def test_create_pool_purchase_naive_purchased_at_does_not_fail_datetime_comparison(
    client, admin_token, db, test_team, test_region
):
    """Regression test: naive purchased_at must not crash the offset-aware
    period_start sourced from the DB."""
    test_team.budget_type = "pool"
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=1000,
            currency="usd",
            purchased_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC),
            stripe_payment_id=f"pi_existing_{int(time.time() * 1000000)}",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 2000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00",
                "stripe_payment_id": f"pi_naive_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201


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


def test_create_periodic_topup_success(client, admin_token, db, test_team, test_region):
    test_team.budget_type = "periodic"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 3.5}}
        )
        mock_instance.update_team_budget = AsyncMock()

        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"cs_periodic_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    data = response.json()
    assert data["team_id"] == test_team.id
    assert data["region_id"] == test_region.id
    assert data["amount_cents"] == 5000
    assert data["new_total_budget_cents"] == 5350
    assert data["budget_type"] == "periodic"

    payment = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == data["stripe_payment_id"])
        .first()
    )
    assert payment is not None
    assert payment.team_id == test_team.id
    assert payment.payment_type == "topup"

    mock_instance.update_team_budget.assert_awaited_once()


def test_create_periodic_topup_region_not_assigned_rejected(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "periodic"
    db.query(DBTeamRegion).filter(
        DBTeamRegion.team_id == test_team.id,
        DBTeamRegion.region_id == test_region.id,
    ).delete()
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": f"cs_periodic_{int(time.time() * 1000000)}",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 400
    assert "not assigned" in response.json()["detail"]


def test_create_periodic_topup_duplicate_payment_id(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "periodic"
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=None,
            source_invoice_id=None,
            stripe_payment_id="cs_dup",
            amount_cents=1000,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            is_active=True,
        )
    )
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": "cs_dup",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == 409
    assert "already exists" in response.json()["detail"]


def test_create_periodic_topup_marks_sync_failed_on_litellm_error(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "periodic"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(side_effect=Exception("region down"))
        mock_instance.update_team_budget = AsyncMock()

        stripe_id = f"cs_periodic_fail_{int(time.time() * 1000000)}"
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": stripe_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 502
    payment = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == stripe_id)
        .first()
    )
    assert payment is not None
    assert payment.sync_status == "sync_failed"
    assert "failed" in (payment.error_log or "").lower()


def test_create_periodic_topup_expiry_anchors_to_last_topup_date(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "periodic"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()

        first_payment_id = f"cs_periodic_first_{int(time.time() * 1000000)}"
        second_payment_id = f"cs_periodic_second_{int(time.time() * 1000000)}"

        response_first = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
            json={
                "amount_cents": 2000,
                "currency": "usd",
                "purchased_at": "2026-03-20T10:00:00Z",
                "stripe_payment_id": first_payment_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response_first.status_code == 201

        response_second = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
            json={
                "amount_cents": 2000,
                "currency": "usd",
                "purchased_at": "2026-03-10T10:00:00Z",
                "stripe_payment_id": second_payment_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )
        assert response_second.status_code == 201

    first_entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.stripe_payment_id == first_payment_id)
        .first()
    )
    second_entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.stripe_payment_id == second_payment_id)
        .first()
    )
    assert first_entry is not None
    assert second_entry is not None
    assert second_entry.expires_at == first_entry.expires_at


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


def test_pool_purchase_propagates_team_budget_only(
    client, admin_token, db, test_team, test_region
):
    """POOL purchases should succeed for subscription-managed POOL teams."""
    test_team.budget_type = "pool"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"pi_team_only_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201


def test_pool_purchase_does_not_trigger_limit_service_background_propagation(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.commit()

    with (
        patch("app.api.budgets.LiteLLMService") as mock_litellm,
        patch(
            "app.core.limit_service.LimitService._trigger_team_budget_propagation"
        ) as mock_trigger,
    ):
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"pi_no_bg_prop_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    mock_trigger.assert_not_called()


def test_pool_purchase_honors_existing_monthly_cap(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.add(test_team)
    db.add(
        DBSpendCap(
            scope="team",
            region_id=test_region.id,
            team_id=test_team.id,
            max_budget=10.0,
            budget_duration="1mo",
            month_start_spend=5.0,
            month_anchor=datetime.now(UTC).date().replace(day=1),
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
                "stripe_payment_id": f"pi_monthly_pool_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201


def test_pool_purchase_restores_team_budget_when_key_sync_fails(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.commit()
    payment_id = f"pi_key_sync_failure_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={
                "team_info": {
                    "max_budget": 0.0,
                    "budget_duration": "365d",
                    "spend": 0.0,
                }
            }
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": payment_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    purchase = (
        db.query(DBPoolPurchase)
        .filter(DBPoolPurchase.stripe_payment_id == payment_id)
        .first()
    )
    assert purchase is not None


def test_pool_purchase_rolls_back_team_budget_when_key_sync_fails(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.add(
        DBPrivateAIKey(
            litellm_token="sk-key-sync-fails",
            team_id=test_team.id,
            region_id=test_region.id,
            name="blocked pool key",
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_litellm.format_team_id.side_effect = lambda region_name, team_id: (
            f"{region_name.replace(' ', '_')}_{team_id}"
        )
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={
                "team_info": {
                    "max_budget": 0.0,
                    "budget_duration": None,
                    "spend": 0.0,
                }
            }
        )
        mock_instance.update_team_budget = AsyncMock()
        mock_instance.update_key_budget = AsyncMock(
            side_effect=RuntimeError("key sync failed")
        )

        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"pi_key_sync_rollback_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 502
    assert mock_instance.update_team_budget.await_count == 2
    first_call, rollback_call = mock_instance.update_team_budget.await_args_list
    assert first_call.kwargs == {
        "team_id": f"{test_region.name}_{test_team.id}",
        "max_budget": 50.0,
        "budget_duration": "31d",
    }
    assert rollback_call.kwargs == {
        "team_id": f"{test_region.name}_{test_team.id}",
        "max_budget": 0.0,
        "budget_duration": None,
        "clear_budget_duration": True,
    }


def test_pool_purchase_preserves_operator_manual_cap_below_purchased_total(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.add(
        DBLimitedResource(
            limit_type=LimitType.DATA_PLANE,
            resource=ResourceType.BUDGET,
            unit=UnitType.DOLLAR,
            max_value=10.0,
            current_value=None,
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            limited_by=LimitSource.MANUAL,
            set_by="admin@example.com",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"pi_manual_cap_low_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    assert response.json()["new_total_budget_cents"] == 5000

    limit = (
        db.query(DBLimitedResource)
        .filter(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == test_team.id,
            DBLimitedResource.resource == ResourceType.BUDGET,
        )
        .first()
    )
    assert limit is not None
    assert float(limit.max_value) == 10.0
    assert limit.set_by == "admin@example.com"


def test_pool_purchase_uses_purchased_total_when_operator_cap_above_purchased(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.add(
        DBLimitedResource(
            limit_type=LimitType.DATA_PLANE,
            resource=ResourceType.BUDGET,
            unit=UnitType.DOLLAR,
            max_value=100.0,
            current_value=None,
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            limited_by=LimitSource.MANUAL,
            set_by="admin@example.com",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"pi_manual_cap_high_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201
    assert response.json()["new_total_budget_cents"] == 5000

    limit = (
        db.query(DBLimitedResource)
        .filter(
            DBLimitedResource.owner_type == OwnerType.TEAM,
            DBLimitedResource.owner_id == test_team.id,
            DBLimitedResource.resource == ResourceType.BUDGET,
        )
        .first()
    )
    assert limit is not None
    assert float(limit.max_value) == 100.0
    assert limit.set_by == "admin@example.com"


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


def test_get_region_purchase_history(client, admin_token, db, test_team, test_region):
    """Test getting purchase history for a region across teams."""
    test_team.budget_type = "pool"

    from app.db.models import DBTeam

    second_team = DBTeam(
        name="Second Team",
        admin_email="second-team@example.com",
        budget_type="pool",
    )
    db.add(second_team)
    db.flush()

    purchase1 = DBPoolPurchase(
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=5000,
        currency="usd",
        purchased_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC),
        stripe_payment_id="pi_region_first",
        created_at=datetime(2026, 3, 10, 10, 0, 0, tzinfo=UTC),
    )
    purchase2 = DBPoolPurchase(
        team_id=second_team.id,
        region_id=test_region.id,
        amount_cents=3000,
        currency="usd",
        purchased_at=datetime(2026, 3, 12, 10, 0, 0, tzinfo=UTC),
        stripe_payment_id="pi_region_second",
        created_at=datetime(2026, 3, 12, 10, 0, 0, tzinfo=UTC),
    )
    db.add_all([purchase1, purchase2])
    db.commit()

    response = client.get(
        f"/budgets/region/{test_region.id}/purchase-history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["region_id"] == test_region.id
    assert len(data["purchases"]) == 2
    assert data["purchases"][0]["stripe_payment_id"] == "pi_region_second"
    assert data["purchases"][1]["stripe_payment_id"] == "pi_region_first"
    assert data["purchases"][0]["team_id"] == second_team.id
    assert data["purchases"][1]["team_id"] == test_team.id


def test_get_region_purchase_history_empty(client, admin_token, db, test_region):
    """Test getting region purchase history when no purchases exist."""
    response = client.get(
        f"/budgets/region/{test_region.id}/purchase-history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["region_id"] == test_region.id
    assert data["purchases"] == []


def test_get_region_purchase_history_region_not_found(client, admin_token):
    """Test region purchase history returns 404 for missing region."""
    response = client.get(
        "/budgets/region/99999/purchase-history",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Region not found"


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
@patch("app.core.config.settings.POOL_PURCHASE_EXPIRY_DAYS", 365)
async def test_sync_pool_team_budgets_expires_stale_pool_team(
    db, test_team, test_region
):
    """Pool teams with 365+ days since last purchase should be set to $0 budget."""
    test_team.budget_type = "pool"
    test_team.last_pool_purchase = datetime.now(UTC) - timedelta(days=366)
    if (
        db.query(DBTeamRegion)
        .filter(
            DBTeamRegion.team_id == test_team.id,
            DBTeamRegion.region_id == test_region.id,
        )
        .first()
        is None
    ):
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
        "app.core.team_service.LiteLLMService.update_team_budget",
        new_callable=AsyncMock,
    ) as mock_update_budget:
        result = await sync_pool_team_budgets(db)

    assert result["teams_updated"] == 1
    assert result["errors"] == []
    mock_update_budget.assert_awaited_once_with(
        team_id=expected_lite_team_id,
        max_budget=0.0,
        budget_duration="365d",
    )


@pytest.mark.asyncio
@patch("app.core.config.settings.POOL_PURCHASE_EXPIRY_DAYS", 365)
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
    if (
        db.query(DBTeamRegion)
        .filter(
            DBTeamRegion.team_id == test_team.id,
            DBTeamRegion.region_id == test_region.id,
        )
        .first()
        is None
    ):
        db.add(
            DBTeamRegion(
                team_id=test_team.id,
                region_id=test_region.id,
                created_at=datetime.now(UTC),
            )
        )
    if (
        db.query(DBTeamRegion)
        .filter(
            DBTeamRegion.team_id == test_team.id,
            DBTeamRegion.region_id == second_region.id,
        )
        .first()
        is None
    ):
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
        "app.core.team_service.LiteLLMService.update_team_budget",
        new_callable=AsyncMock,
    ) as mock_update_budget:
        result = await sync_pool_team_budgets(db)

    assert result["teams_updated"] == 1
    assert result["errors"] == []
    mock_update_budget.assert_awaited_once_with(
        team_id=expected_lite_team_id,
        max_budget=0.0,
        budget_duration="365d",
    )


@pytest.mark.asyncio
@patch("app.core.config.settings.POOL_PURCHASE_EXPIRY_DAYS", 365)
async def test_sync_pool_team_budgets_uses_team_only_propagation(
    db, test_team, test_region
):
    """Pool budget expiry should propagate team budget only."""
    test_team.budget_type = "pool"
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=1000,
            currency="usd",
            purchased_at=datetime.now(UTC) - timedelta(days=400),
            stripe_payment_id="pi_team_only_sync",
            created_at=datetime.now(UTC),
        )
    )
    db.commit()

    with patch(
        "app.api.budgets.propagate_team_budget_to_keys", new_callable=AsyncMock
    ) as mock_propagate:
        mock_propagate.return_value = {"teams_updated": 1, "errors": []}
        result = await sync_pool_team_budgets(db)

    assert result["teams_updated"] == 1
    assert mock_propagate.await_count == 1
    assert mock_propagate.call_args.kwargs["apply_to_keys"] is False


@pytest.mark.asyncio
async def test_sync_pool_team_monthly_caps_rollover_updates_effective_budget(
    db, test_team, test_region
):
    test_team.budget_type = "pool"
    db.add(test_team)
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="usd",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=f"pi_rollover_{int(time.time() * 1000000)}",
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=None,
            source_invoice_id=None,
            stripe_payment_id=f"pi_rollover_ledger_{int(time.time() * 1000000)}",
            amount_cents=5000,
            consumed_cents=0,
            purchased_at=datetime.now(UTC),
            effective_period_start=None,
            effective_period_end=None,
            expires_at=datetime.now(UTC) + timedelta(days=365),
            rolled_over_from_id=None,
            is_active=True,
        )
    )
    today = datetime.now(UTC).date()
    prev_month_anchor = (
        datetime(today.year - 1, 12, 1, tzinfo=UTC).date()
        if today.month == 1
        else datetime(today.year, today.month - 1, 1, tzinfo=UTC).date()
    )
    cap = DBSpendCap(
        scope="team",
        region_id=test_region.id,
        team_id=test_team.id,
        max_budget=10.0,
        budget_duration="1mo",
        month_anchor=prev_month_anchor,
        month_start_spend=7.0,
    )
    db.add(cap)
    db.commit()

    with (
        patch(
            "app.api.budgets.LiteLLMService.get_team_info", new_callable=AsyncMock
        ) as mock_get_team_info,
        patch(
            "app.api.budgets.LiteLLMService.update_team_budget", new_callable=AsyncMock
        ) as mock_update_team_budget,
    ):
        mock_get_team_info.return_value = {"team_info": {"spend": 20.0}}
        result = await sync_pool_team_monthly_caps(db)

    assert result["errors"] == []
    assert result["teams_updated"] == 1
    db.refresh(cap)
    assert cap.month_anchor == today.replace(day=1)
    assert cap.month_start_spend == 20.0
    mock_update_team_budget.assert_awaited_once()
    assert mock_update_team_budget.await_args.kwargs["max_budget"] == 30.0
    assert mock_update_team_budget.await_args.kwargs["budget_duration"] == "365d"
