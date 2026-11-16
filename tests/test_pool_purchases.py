import time

from app.db.models import (
    DBLimitedResource,
    DBPoolPurchase,
    DBPrivateAIKey,
    DBSpendCap,
    DBPeriodicPayment,
)
from app.db.models import DBPeriodicBudgetLedgerEntry
from app.db.models import DBTeamRegion, DBTeamSpendPeriod
from app.schemas.limits import LimitSource, LimitType, OwnerType, ResourceType, UnitType
from datetime import datetime, UTC, timedelta
import pytest
from fastapi import HTTPException
from sqlalchemy.exc import IntegrityError
from unittest.mock import patch, AsyncMock
from app.api.budgets import (
    _sync_pool_key_effective_budgets,
    sync_pool_team_budgets,
)
from app.core.config import settings
from app.core.periodic_budget_ledger_service import compute_active_topup_remaining


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


def test_create_periodic_topup_duplicate_payment_integrity_error_returns_409(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "periodic"
    db.commit()

    class _OrigUniqueError(Exception):
        pgcode = "23505"

        class diag:  # noqa: N801
            constraint_name = "ix_periodic_payments_stripe_payment_id"

    with patch(
        "app.api.budgets.add_topup_entry",
        side_effect=IntegrityError("", {}, _OrigUniqueError()),
    ):
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"cs_periodic_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": "Bearer " + admin_token},
        )

    assert response.status_code == 409
    assert (
        response.json()["detail"]
        == "A purchase with this stripe_payment_id already exists"
    )


def test_create_periodic_topup_non_stripe_integrity_error_returns_500(
    client, admin_token, db, test_team, test_region
):
    test_team.budget_type = "periodic"
    db.commit()

    class _OrigIntegrityError(Exception):
        pgcode = "23505"

        class diag:  # noqa: N801
            constraint_name = "ix_periodic_payments_team_id"

        def __str__(self):
            return "duplicate key detail includes stripe_payment_id value"

    with patch(
        "app.api.budgets.add_topup_entry",
        side_effect=IntegrityError("", {}, _OrigIntegrityError()),
    ):
        response = client.post(
            f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
            json={
                "amount_cents": 5000,
                "currency": "usd",
                "purchased_at": "2026-03-13T10:00:00Z",
                "stripe_payment_id": f"cs_periodic_{int(time.time() * 1000000)}",
            },
            headers={"Authorization": "Bearer " + admin_token},
        )

    assert response.status_code == 500
    assert response.json()["detail"] == "Failed to record periodic top-up purchase"


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


def test_create_periodic_topup_retry_after_sync_failure_succeeds(
    client, admin_token, db, test_team, test_region
):
    """Issue #617A: a top-up whose LiteLLM sync failed must be resubmittable
    with the same stripe_payment_id. The failed attempt must not leave a ledger
    entry behind (which would block the retry with a permanent 409)."""
    test_team.budget_type = "periodic"
    db.commit()

    stripe_id = f"cs_periodic_retry_{int(time.time() * 1000000)}"
    payload = {
        "amount_cents": 5000,
        "currency": "usd",
        "purchased_at": "2026-03-13T10:00:00Z",
        "stripe_payment_id": stripe_id,
    }
    url = f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic"
    headers = {"Authorization": f"Bearer {admin_token}"}

    # First attempt: LiteLLM sync fails -> 502, payment stamped sync_failed.
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(side_effect=Exception("region down"))
        mock_instance.update_team_budget = AsyncMock()
        first = client.post(url, json=payload, headers=headers)
    assert first.status_code == 502

    # The failed ledger entry must not linger — it would otherwise trip the
    # duplicate guard / unique constraint on the retry.
    leftover = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.stripe_payment_id == stripe_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup",
        )
        .all()
    )
    assert leftover == []

    # Second attempt with the SAME stripe_payment_id: LiteLLM healthy -> applied.
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 0.0}}
        )
        mock_instance.update_team_budget = AsyncMock()
        second = client.post(url, json=payload, headers=headers)
    assert second.status_code == 201, second.text

    entry = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.stripe_payment_id == stripe_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup",
        )
        .one()
    )
    assert entry.is_active is True
    assert entry.amount_cents == 5000
    payment = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == stripe_id)
        .first()
    )
    assert payment.sync_status == "success"


def test_pool_purchase_retry_removes_lingering_ledger_entry(
    client, admin_token, db, test_team, test_region
):
    """Issue #617A: purchase_pool_budget's retry cleanup must delete a lingering
    topup ledger entry from a prior sync_failed attempt. Deleting the payment
    only SET NULLs the ledger's source_payment_id (FK ondelete), so without
    explicit cleanup the ledger row survives and blocks the retry (permanent
    409)."""
    test_team.budget_type = "pool"
    db.commit()

    stripe_id = f"pi_pool_stale_{int(time.time() * 1000000)}"

    # Simulate the rows a prior sync_failed pool purchase would leave behind.
    payment = DBPeriodicPayment(
        team_id=test_team.id,
        stripe_payment_id=stripe_id,
        amount_cents=5000,
        currency="usd",
        payment_type="topup",
        status="completed",
        sync_status="sync_failed",
        payment_date=datetime.now(UTC),
    )
    db.add(payment)
    db.flush()
    db.add(
        DBPoolPurchase(
            team_id=test_team.id,
            region_id=test_region.id,
            amount_cents=5000,
            currency="usd",
            purchased_at=datetime.now(UTC),
            stripe_payment_id=stripe_id,
            created_at=datetime.now(UTC),
        )
    )
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="topup",
            source_payment_id=payment.id,
            stripe_payment_id=stripe_id,
            amount_cents=5000,
            consumed_cents=5000,
            purchased_at=datetime.now(UTC),
            is_active=False,
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
                "stripe_payment_id": stripe_id,
            },
            headers={"Authorization": f"Bearer {admin_token}"},
        )

    assert response.status_code == 201, response.text

    entries = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.stripe_payment_id == stripe_id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup",
        )
        .all()
    )
    # The stale row is gone; exactly one active entry from the fresh run remains.
    assert len(entries) == 1
    assert entries[0].is_active is True


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


def test_create_pool_purchase_inactive_region_rejected(
    client, admin_token, db, test_team, test_region
):
    """A region being decommissioned must not accept new pool purchases."""
    test_team.budget_type = "pool"
    test_region.is_active = False
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": f"pi_inactive_{int(time.time() * 1000000)}",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Region not found or inactive"

    # Nothing was recorded against the dead region.
    assert (
        db.query(DBPoolPurchase)
        .filter(DBPoolPurchase.region_id == test_region.id)
        .count()
        == 0
    )


def test_create_periodic_topup_inactive_region_rejected(
    client, admin_token, db, test_team, test_region
):
    """A region being decommissioned must not accept new top-ups either."""
    test_team.budget_type = "periodic"
    test_region.is_active = False
    db.commit()

    response = client.post(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase/periodic",
        json={
            "amount_cents": 5000,
            "currency": "usd",
            "purchased_at": "2026-03-13T10:00:00Z",
            "stripe_payment_id": f"cs_inactive_{int(time.time() * 1000000)}",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 404
    assert response.json()["detail"] == "Region not found or inactive"

    # The inactive check runs before the team-region assignment check, so no
    # ledger entry is written.
    assert (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.region_id == test_region.id)
        .count()
        == 0
    )


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


@patch("app.core.team_service.effective_team_group_slugs", return_value=["group-a"])
def test_pool_purchase_recreates_a_team_missing_in_litellm(
    _mock_slugs, client, admin_token, db, test_team, test_region
):
    """A team LiteLLM lost must be rebuilt, not turned into a failed purchase."""
    test_team.budget_type = "pool"
    db.commit()
    payment_id = f"pi_missing_team_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            side_effect=[
                HTTPException(status_code=404, detail="Team not found"),
                {"team_info": {"max_budget": 0.0, "spend": 0.0}},
            ]
        )
        mock_instance.create_team = AsyncMock()
        mock_instance.create_user = AsyncMock()
        mock_instance.add_team_member = AsyncMock()
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
    assert mock_instance.create_team.await_count == 1
    assert mock_instance.get_team_info.await_count == 2
    mock_instance.update_team_budget.assert_awaited_once()
    payment = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == payment_id)
        .first()
    )
    assert payment.sync_status == "success"


@patch("app.core.team_service.effective_team_group_slugs", return_value=["group-a"])
def test_pool_purchase_reanchors_member_caps_after_a_team_recreate(
    _mock_slugs, client, admin_token, db, test_team, test_region, test_team_user
):
    """A rebuilt membership carries no member budget, so the cap goes back on."""
    test_team.budget_type = "pool"
    db.add(
        DBSpendCap(
            scope="team_member",
            region_id=test_region.id,
            team_id=test_team.id,
            user_id=test_team_user.id,
            max_budget=5.0,
        )
    )
    db.commit()
    payment_id = f"pi_missing_team_caps_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            side_effect=[
                HTTPException(status_code=404, detail="Team not found"),
                {
                    "team_info": {"max_budget": 0.0, "spend": 0.0},
                    "team_memberships": [
                        {"user_id": str(test_team_user.id), "spend": 2.0}
                    ],
                },
            ]
        )
        mock_instance.create_team = AsyncMock()
        mock_instance.create_user = AsyncMock()
        mock_instance.add_team_member = AsyncMock()
        mock_instance.update_team_member = AsyncMock()
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
    mock_instance.update_team_member.assert_awaited_once()
    kwargs = mock_instance.update_team_member.await_args.kwargs
    assert kwargs["user_id"] == str(test_team_user.id)
    assert kwargs["max_budget_in_team"] == 7.0


@patch("app.core.team_service.effective_team_group_slugs", return_value=["group-a"])
def test_pool_purchase_fails_when_member_caps_cannot_be_restored_after_recreate(
    _mock_slugs, client, admin_token, db, test_team, test_region, test_team_user
):
    """A recreate that cannot restore the caps must leave the purchase retryable."""
    test_team.budget_type = "pool"
    db.add(
        DBSpendCap(
            scope="team_member",
            region_id=test_region.id,
            team_id=test_team.id,
            user_id=test_team_user.id,
            max_budget=5.0,
        )
    )
    db.commit()
    payment_id = f"pi_missing_team_caps_fail_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = AsyncMock(
            side_effect=[
                HTTPException(status_code=404, detail="Team not found"),
                {
                    "team_info": {"max_budget": 0.0, "spend": 0.0},
                    "team_memberships": [
                        {"user_id": str(test_team_user.id), "spend": 2.0}
                    ],
                },
            ]
        )
        mock_instance.create_team = AsyncMock()
        mock_instance.create_user = AsyncMock()
        mock_instance.add_team_member = AsyncMock()
        mock_instance.update_team_member = AsyncMock(
            side_effect=Exception("member push failed")
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

    assert response.status_code == 502
    mock_instance.update_team_budget.assert_not_awaited()
    payment = (
        db.query(DBPeriodicPayment)
        .filter(DBPeriodicPayment.stripe_payment_id == payment_id)
        .first()
    )
    assert payment.sync_status == "sync_failed"
    assert "Member caps could not be restored" in payment.error_log
    # The failed top-up entry is gone, so a retry can insert it again.
    assert (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.stripe_payment_id == payment_id)
        .first()
        is None
    )


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
        "clear_budget_duration": True,
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


def test_pool_purchase_locks_region_row_against_concurrent_deactivation(
    client, admin_token, db, test_team, test_region
):
    """
    The region check must take a row lock, not just read.

    The purchase path awaits LiteLLM before it commits. Without `FOR UPDATE`, a
    deactivation committing inside that window would leave the payment and
    ledger rows against an already-inactive region. Postgres enforces the
    blocking itself — what we verify here is that the lock is actually
    requested, which is the part that lives in our code.
    """
    from sqlalchemy import event

    # Bind from the live session — importing conftest here would build a second
    # engine object and the listener would never fire.
    engine = db.get_bind()
    statements: list[str] = []

    def record(conn, cursor, statement, params, context, executemany):
        statements.append(statement)

    test_team.budget_type = "pool"
    db.commit()

    event.listen(engine, "before_cursor_execute", record)
    try:
        with patch("app.api.budgets.LiteLLMService") as mock_litellm:
            mock_instance = mock_litellm.return_value
            mock_instance.update_team_budget = AsyncMock()
            mock_instance.get_team_info = AsyncMock(
                return_value={"team_info": {"spend": 0.0, "max_budget": 0.0}}
            )
            client.post(
                f"/budgets/region/{test_region.id}/teams/{test_team.id}/purchase",
                json={
                    "amount_cents": 5000,
                    "currency": "usd",
                    "purchased_at": "2026-03-13T10:00:00Z",
                    "stripe_payment_id": f"pi_lock_{int(time.time() * 1000000)}",
                },
                headers={"Authorization": f"Bearer {admin_token}"},
            )
    finally:
        event.remove(engine, "before_cursor_execute", record)

    region_selects = [
        s for s in statements if "FROM regions" in s and s.strip().startswith("SELECT")
    ]
    assert region_selects, "expected the purchase path to select the region"
    assert any("FOR UPDATE" in s for s in region_selects), (
        "region row must be locked FOR UPDATE so a concurrent deactivation "
        f"cannot commit mid-purchase; got: {region_selects}"
    )


def _seed_pool_keys_for_sync(db, test_team, test_region):
    """One key with a configured cap, one without, both in the same region."""
    test_team.budget_type = "pool"
    db.add(test_team)
    capped = DBPrivateAIKey(
        name="capped-sync-key",
        litellm_token="capped-sync-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    uncapped = DBPrivateAIKey(
        name="uncapped-sync-key",
        litellm_token="uncapped-sync-token",
        region_id=test_region.id,
        team_id=test_team.id,
    )
    db.add_all([capped, uncapped])
    db.commit()
    db.add(
        DBSpendCap(
            scope="key",
            region_id=test_region.id,
            team_id=test_team.id,
            key_id=capped.id,
            max_budget=25.0,
            budget_duration="1mo",
        )
    )
    db.commit()
    return capped, uncapped


@pytest.mark.asyncio
async def test_sync_pool_keys_resets_spend_on_purchase(db, test_team, test_region):
    """A top-up starts a new cap period: spend=0.0 and no LiteLLM duration."""
    capped, uncapped = _seed_pool_keys_for_sync(db, test_team, test_region)

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.update_key_budget = AsyncMock()
        mock_instance.update_key_duration = AsyncMock()
        errors = await _sync_pool_key_effective_budgets(
            db, team_id=test_team.id, region=test_region, purchased_total=50.0
        )

    assert errors == []
    budget_calls = [
        call
        for call in mock_instance.update_key_budget.await_args_list
        if call.kwargs["spend"] is None
    ]
    reset_calls = [
        call
        for call in mock_instance.update_key_budget.await_args_list
        if call.kwargs["spend"] == 0.0
    ]
    by_token = {call.kwargs["litellm_token"]: call.kwargs for call in budget_calls}
    assert len(by_token) == 2
    capped_kwargs = by_token[capped.litellm_token]
    assert capped_kwargs["max_budget"] == 25.0
    assert capped_kwargs.get("budget_duration") is None
    assert capped_kwargs["clear_budget_duration"] is True
    uncapped_kwargs = by_token[uncapped.litellm_token]
    assert uncapped_kwargs["clear_max_budget"] is True
    assert uncapped_kwargs["clear_budget_duration"] is True
    # The spend reset is a second pass, once per key and nothing else.
    assert sorted(call.kwargs["litellm_token"] for call in reset_calls) == sorted(
        [capped.litellm_token, uncapped.litellm_token]
    )
    for call in reset_calls:
        assert set(call.kwargs) == {"litellm_token", "spend"}

    duration_tokens = {
        call.kwargs["litellm_token"]: call.kwargs["duration"]
        for call in mock_instance.update_key_duration.await_args_list
    }
    expected = f"{settings.POOL_PURCHASE_EXPIRY_DAYS}d"
    assert duration_tokens == {
        capped.litellm_token: expected,
        uncapped.litellm_token: expected,
    }


@pytest.mark.asyncio
async def test_sync_pool_keys_skips_spend_reset_when_a_budget_write_fails(
    db, test_team, test_region
):
    """A failed budget write must leave every key counter untouched."""
    capped, _uncapped = _seed_pool_keys_for_sync(db, test_team, test_region)

    async def _budget(**kwargs):
        if kwargs["litellm_token"] == capped.litellm_token:
            raise RuntimeError("litellm down")
        return None

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.update_key_budget = AsyncMock(side_effect=_budget)
        mock_instance.update_key_duration = AsyncMock()
        errors = await _sync_pool_key_effective_budgets(
            db, team_id=test_team.id, region=test_region, purchased_total=50.0
        )

    assert len(errors) == 1
    for call in mock_instance.update_key_budget.await_args_list:
        assert call.kwargs.get("spend") != 0.0


@pytest.mark.asyncio
async def test_sync_pool_keys_keeps_spend_on_expiry(db, test_team, test_region):
    """Expiry sync only re-applies the cap; it must not reset key spend."""
    _seed_pool_keys_for_sync(db, test_team, test_region)

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.update_key_budget = AsyncMock()
        mock_instance.update_key_duration = AsyncMock()
        errors = await _sync_pool_key_effective_budgets(
            db, team_id=test_team.id, region=test_region, purchased_total=0.0
        )

    assert errors == []
    assert len(mock_instance.update_key_budget.await_args_list) == 2
    for call in mock_instance.update_key_budget.await_args_list:
        assert call.kwargs["spend"] is None
        assert call.kwargs.get("budget_duration") is None
        assert call.kwargs["clear_budget_duration"] is True
    mock_instance.update_key_duration.assert_not_awaited()


def _buy(
    client,
    admin_token,
    team,
    region,
    amount_cents,
    *,
    days_ago=0,
    payment_id=None,
    expected_status=201,
):
    purchased_at = datetime.now(UTC) - timedelta(days=days_ago)
    response = client.post(
        f"/budgets/region/{region.id}/teams/{team.id}/purchase",
        json={
            "amount_cents": amount_cents,
            "currency": "usd",
            "purchased_at": purchased_at.isoformat().replace("+00:00", "Z"),
            "stripe_payment_id": payment_id
            or f"pi_settle_{int(time.time() * 1000000)}",
        },
        headers={"Authorization": f"Bearer {admin_token}"},
    )
    assert response.status_code == expected_status, response.text
    return response.json()


def _topup_entries(db, team, region):
    return (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(
            DBPeriodicBudgetLedgerEntry.team_id == team.id,
            DBPeriodicBudgetLedgerEntry.region_id == region.id,
            DBPeriodicBudgetLedgerEntry.entry_type == "topup",
        )
        .order_by(DBPeriodicBudgetLedgerEntry.purchased_at.asc())
        .all()
    )


def _team_info(*spends):
    return AsyncMock(side_effect=[{"team_info": {"spend": s}} for s in spends])


def test_topup_after_spend_grants_only_new_credit(
    client, admin_token, db, test_team, test_region
):
    """Buy $50, spend $50, buy $10: the team gets $10 of room, not $60."""
    test_team.budget_type = "pool"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 50.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=1)
        data = _buy(client, admin_token, test_team, test_region, 1000)

    assert data["new_total_budget_cents"] == 6000
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 60.0
    first = _topup_entries(db, test_team, test_region)[0]
    assert first.consumed_cents == 5000
    assert first.is_active is False


def test_topup_after_partial_spend_keeps_unspent_credit(
    client, admin_token, db, test_team, test_region
):
    """Buy $100, spend $79.93, buy $100: $120.07 of room, cap $200."""
    data = _buy_then_spend_7993(client, admin_token, db, test_team, test_region)

    assert data["new_total_budget_cents"] == 20000
    assert (
        compute_active_topup_remaining(
            db, team_id=test_team.id, region_id=test_region.id
        )
        == 12007
    )


def test_topups_without_spend_add_up_and_ignore_pre_ledger_spend(
    client, admin_token, db, test_team, test_region
):
    """Spend from before the first purchase is not charged to purchased credit."""
    test_team.budget_type = "pool"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(12.0, 12.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 3000, days_ago=1)
        _buy(client, admin_token, test_team, test_region, 2000)

    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 62.0
    assert all(
        e.consumed_cents == 0 for e in _topup_entries(db, test_team, test_region)
    )


def test_topup_with_active_subscription_keeps_current_projection(
    client, admin_token, db, test_team, test_region
):
    """Subscription teams settle at invoice close, so the purchase charges nothing."""
    test_team.budget_type = "pool"
    now = datetime.now(UTC)
    db.add(
        DBPeriodicBudgetLedgerEntry(
            team_id=test_team.id,
            region_id=test_region.id,
            entry_type="subscription",
            amount_cents=10000,
            consumed_cents=0,
            is_active=True,
            purchased_at=now - timedelta(days=5),
            effective_period_start=now - timedelta(days=5),
            effective_period_end=now + timedelta(days=25),
            expires_at=now + timedelta(days=25),
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(40.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 5000)

    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 190.0
    entries = (
        db.query(DBPeriodicBudgetLedgerEntry)
        .filter(DBPeriodicBudgetLedgerEntry.team_id == test_team.id)
        .all()
    )
    assert all(e.consumed_cents == 0 for e in entries)
    assert (
        db.query(DBTeamSpendPeriod)
        .filter(DBTeamSpendPeriod.source == "topup_settlement")
        .count()
        == 0
    )


def test_topup_team_without_snapshot_is_settled_from_its_next_purchase(
    client, admin_token, db, test_team, test_region
):
    """A team with an earlier top-up and no snapshot is charged $0 at its first
    settlement; the next purchase charges only the spend since then."""
    test_team.budget_type = "pool"
    now = datetime.now(UTC)
    seeded = DBPeriodicBudgetLedgerEntry(
        team_id=test_team.id,
        region_id=test_region.id,
        entry_type="topup",
        amount_cents=10000,
        consumed_cents=0,
        is_active=True,
        purchased_at=now - timedelta(days=10),
        expires_at=now + timedelta(days=settings.POOL_PURCHASE_EXPIRY_DAYS - 10),
    )
    db.add(seeded)
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(60.0, 75.0)
        mock_instance.get_team_spend_in_range = AsyncMock(return_value=0.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 10000, days_ago=1)
        first_cap = mock_instance.update_team_budget.await_args.kwargs["max_budget"]
        _buy(client, admin_token, test_team, test_region, 1000)

    assert first_cap == 260.0
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 270.0
    db.refresh(seeded)
    assert seeded.consumed_cents == 1500
    mock_instance.get_team_spend_in_range.assert_not_awaited()


def test_topup_after_another_topup_expired_does_not_give_back_spend(
    client, admin_token, db, test_team, test_region
):
    """The oldest top-up expires partly used: only its unused part is lost, and
    spend already charged is not given back."""
    test_team.budget_type = "pool"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 5.0, 30.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 2000, days_ago=2)
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=1)
        entry_a, entry_b = _topup_entries(db, test_team, test_region)
        assert entry_a.consumed_cents == 500
        entry_a.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
        _buy(client, admin_token, test_team, test_region, 1000)

    # 30 spent + 25 left on B + 10 new; the $15 left on A expired.
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 65.0
    db.refresh(entry_b)
    assert entry_b.consumed_cents == 2500


def test_topup_after_every_held_topup_expired_charges_nothing(
    client, admin_token, db, test_team, test_region
):
    """Buy $100, spend $60, the top-up expires, buy $100: the expired credit paid
    for the $60, so the new top-up gives the full $100 of room."""
    test_team.budget_type = "pool"
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 60.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 10000, days_ago=1)
        (expired,) = _topup_entries(db, test_team, test_region)
        expired.expires_at = datetime.now(UTC) - timedelta(minutes=1)
        db.commit()
        _buy(client, admin_token, test_team, test_region, 10000)

    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 160.0
    assert [e.consumed_cents for e in _topup_entries(db, test_team, test_region)] == [
        0,
        0,
    ]


def test_topup_charges_spend_to_a_rollover_dated_in_the_future(
    client, admin_token, db, test_team, test_region
):
    """A rollover is dated at the end of its period, but it is credit the team
    held at the snapshot, so the spend since then is charged to it."""
    test_team.budget_type = "pool"
    now = datetime.now(UTC)
    rollover = DBPeriodicBudgetLedgerEntry(
        team_id=test_team.id,
        region_id=test_region.id,
        entry_type="topup_rollover",
        amount_cents=5000,
        consumed_cents=0,
        is_active=True,
        purchased_at=now + timedelta(days=20),
        expires_at=now + timedelta(days=200),
    )
    db.add(rollover)
    db.commit()
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type="pool",
            period_start=now,
            period_end=now,
            source="topup_settlement",
            total_spend=10.0,
        )
    )
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(40.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 1000)

    # 40 spent + 20 left; FIFO charges top-ups before rollovers, so the new
    # $10 goes first and the rollover takes the other $20.
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 70.0
    db.refresh(rollover)
    assert rollover.consumed_cents == 2000
    assert [e.consumed_cents for e in _topup_entries(db, test_team, test_region)] == [
        1000
    ]


def test_topup_charges_spend_to_an_entry_created_by_an_overlapping_purchase(
    client, admin_token, db, test_team, test_region
):
    """A concurrent purchase can commit its row with a created_at later than this
    transaction's start. It is still credit the team held, so spend is charged."""
    test_team.budget_type = "pool"
    now = datetime.now(UTC)
    db.add(
        DBTeamSpendPeriod(
            team_id=test_team.id,
            region_id=test_region.id,
            budget_type="pool",
            period_start=now,
            period_end=now,
            source="topup_settlement",
            total_spend=0.0,
        )
    )
    other = DBPeriodicBudgetLedgerEntry(
        team_id=test_team.id,
        region_id=test_region.id,
        entry_type="topup",
        amount_cents=5000,
        consumed_cents=0,
        is_active=True,
        purchased_at=now - timedelta(hours=1),
        expires_at=now + timedelta(days=300),
        created_at=now + timedelta(hours=1),
    )
    db.add(other)
    db.commit()

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(30.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 1000)

    # 30 spent + 20 left on the other top-up + 10 new.
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 60.0
    db.refresh(other)
    assert other.consumed_cents == 3000


def test_topup_retry_after_failed_budget_write_does_not_charge_spend_again(
    client, admin_token, db, test_team, test_region
):
    """A failed purchase undoes its settlement, so the retry charges the spend once."""
    test_team.budget_type = "pool"
    db.commit()
    payment_id = f"pi_settle_retry_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 30.0, 30.0)
        mock_instance.update_team_budget = AsyncMock(
            side_effect=[None, RuntimeError("LiteLLM down"), None]
        )
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=1)
        _buy(
            client,
            admin_token,
            test_team,
            test_region,
            2000,
            payment_id=payment_id,
            expected_status=502,
        )
        _buy(client, admin_token, test_team, test_region, 2000, payment_id=payment_id)

    # 30 spent + 20 left on the first top-up + 20 new.
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 70.0
    first, retried = _topup_entries(db, test_team, test_region)
    assert first.consumed_cents == 3000
    assert retried.consumed_cents == 0


def test_failed_topup_write_undoes_the_settlement_that_charged_the_new_row(
    client, admin_token, db, test_team, test_region
):
    """A purchase dated before the first one is charged first by FIFO. When its
    LiteLLM write fails, that charge and the snapshot go away with the row."""
    test_team.budget_type = "pool"
    db.commit()
    payment_id = f"pi_settle_undo_{int(time.time() * 1000000)}"

    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 79.93, 79.93)
        mock_instance.update_team_budget = AsyncMock(
            side_effect=[None, RuntimeError("LiteLLM down"), None]
        )
        _buy(client, admin_token, test_team, test_region, 10000, days_ago=1)
        _buy(
            client,
            admin_token,
            test_team,
            test_region,
            5000,
            days_ago=5,
            payment_id=payment_id,
            expected_status=502,
        )

        (first,) = _topup_entries(db, test_team, test_region)
        assert first.consumed_cents == 0
        assert (
            db.query(DBTeamSpendPeriod)
            .filter(DBTeamSpendPeriod.source == "topup_settlement")
            .count()
            == 1
        )

        _buy(
            client,
            admin_token,
            test_team,
            test_region,
            5000,
            days_ago=5,
            payment_id=payment_id,
        )

    # 79.93 spent: 50 on the older retried row, 29.93 on the first; 70.07 left.
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 150.0
    retried, first = _topup_entries(db, test_team, test_region)
    assert retried.consumed_cents == 5000
    assert first.consumed_cents == 2993


def _buy_then_spend_7993(client, admin_token, db, team, region):
    """Criterion 2 flow: buy $100, spend $79.93, buy $100."""
    team.budget_type = "pool"
    db.commit()
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 79.93)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, team, region, 10000, days_ago=1)
        return _buy(client, admin_token, team, region, 10000)


def _clear_team_budget(client, admin_token, team, region):
    with (
        patch(
            "app.api.spend.LiteLLMService.update_team_budget", new_callable=AsyncMock
        ) as mock_update,
        patch(
            "app.api.spend.LiteLLMService.get_team_info",
            new_callable=AsyncMock,
            return_value={"team_info": {}},
        ),
    ):
        response = client.post(
            f"/spend/{region.id}/team/{team.id}/budget/clear",
            headers={"Authorization": f"Bearer {admin_token}"},
        )
    assert response.status_code == 200, response.text
    return mock_update.await_args.kwargs["max_budget"]


def test_periodic_status_reports_settled_topup_remaining(
    client, admin_token, db, test_team, test_region
):
    _buy_then_spend_7993(client, admin_token, db, test_team, test_region)

    response = client.get(
        f"/budgets/region/{test_region.id}/teams/{test_team.id}/periodic-status",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    data = response.json()
    assert data["topup_remaining_cents"] == 12007
    assert data["desired_remaining_cents"] == 12007


def test_team_budget_clear_after_expiry_does_not_give_back_spend(
    client, admin_token, db, test_team, test_region
):
    """The clear path reads the last settlement, so an expired top-up stays gone."""
    test_team.budget_type = "pool"
    db.commit()
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 5.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 2000, days_ago=2)
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=1)
    entry_a, _ = _topup_entries(db, test_team, test_region)
    entry_a.expires_at = datetime.now(UTC) - timedelta(minutes=1)
    db.commit()

    # Last settled counter 5 + the 50 left on B; the $15 left on A expired.
    assert _clear_team_budget(client, admin_token, test_team, test_region) == 55.0


def test_team_budget_clear_restores_settled_cap(
    client, admin_token, db, test_team, test_region
):
    _buy_then_spend_7993(client, admin_token, db, test_team, test_region)

    assert _clear_team_budget(client, admin_token, test_team, test_region) == 200.0


def _alert_window_and_budget(db, team, region):
    from app.core.budget_alert_service import _team_budget
    from app.core.spend_period_service import resolve_team_period_window

    db.refresh(team)
    window = resolve_team_period_window(db, team, region.id)
    return window.period_start, _team_budget(db, team, region.id)


def test_alert_budget_keeps_a_used_up_topup_in_the_window(
    client, admin_token, db, test_team, test_region
):
    """Buy $50, spend $50, buy $10: the alert reads $50 of $60, not $50 of $10."""
    test_team.budget_type = "pool"
    db.commit()
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 50.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=2)
        _buy(client, admin_token, test_team, test_region, 1000)
    first = _topup_entries(db, test_team, test_region)[0]
    assert first.is_active is False

    window_start, budget = _alert_window_and_budget(db, test_team, test_region)

    assert window_start == first.purchased_at
    assert budget == 60.0


def test_alert_budget_after_three_topups_leaves_true_room(
    client, admin_token, db, test_team, test_region
):
    """T1 $50, spend $10, T2 $50, spend $60 more, T3 $10: $70 of $110, $40 room."""
    test_team.budget_type = "pool"
    db.commit()
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(0.0, 10.0, 70.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=3)
        _buy(client, admin_token, test_team, test_region, 5000, days_ago=2)
        _buy(client, admin_token, test_team, test_region, 1000, days_ago=1)
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 110.0
    first = _topup_entries(db, test_team, test_region)[0]

    window_start, budget = _alert_window_and_budget(db, test_team, test_region)

    assert window_start == first.purchased_at
    assert budget == 110.0


def test_team_budget_clear_after_cancel_uses_newest_settlement(
    client, admin_token, db, test_team, test_region
):
    """A cancelled cycle row ends in the future but is older than the settlement."""
    test_team.budget_type = "pool"
    now = datetime.now(UTC)
    for total_spend, source, start, end in (
        (
            10.0,
            "moad_subscription_cycle",
            now - timedelta(days=5),
            now + timedelta(days=26),
        ),
        (
            20.0,
            "moad_subscription_deactivate",
            now - timedelta(days=5),
            now - timedelta(days=1),
        ),
    ):
        db.add(
            DBTeamSpendPeriod(
                team_id=test_team.id,
                region_id=test_region.id,
                budget_type="pool",
                period_start=start,
                period_end=end,
                source=source,
                total_spend=total_spend,
            )
        )
    db.commit()
    with patch("app.api.budgets.LiteLLMService") as mock_litellm:
        mock_instance = mock_litellm.return_value
        mock_instance.get_team_info = _team_info(20.0)
        mock_instance.update_team_budget = AsyncMock()
        _buy(client, admin_token, test_team, test_region, 5000)
    assert mock_instance.update_team_budget.await_args.kwargs["max_budget"] == 70.0

    assert _clear_team_budget(client, admin_token, test_team, test_region) == 70.0


def test_drift_check_finds_no_drift_for_a_settled_topup_team(
    client, admin_token, db, test_team, test_region
):
    """The drift check reads the settled ledger, so the purchase cap matches it.

    Only the billing cycle calls this check, but it can run for a top-up-only team.
    """
    import asyncio

    from app.core.worker import reconcile_periodic_team_budget_drift

    _buy_then_spend_7993(client, admin_token, db, test_team, test_region)

    with patch("app.core.worker.LiteLLMService") as mock_litellm:
        mock_litellm.return_value.get_team_info = AsyncMock(
            return_value={"team_info": {"spend": 79.93, "max_budget": 200.0}}
        )
        result = asyncio.run(
            reconcile_periodic_team_budget_drift(
                db=db, team=test_team, region=test_region
            )
        )

    assert result.expected_max_budget_cents == 20000
    assert result.drift_cents == 0
