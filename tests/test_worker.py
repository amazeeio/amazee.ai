import asyncio
import pytest
from app.db.models import (
    DBPrivateAIKey,
    DBTeam,
    DBTeamMetrics,
    DBLimitedResource,
    DBPoolPurchase,
    DBPeriodicBudgetLedgerEntry,
)
from app.schemas.models import BudgetType
from datetime import datetime, UTC, timedelta
from app.core.worker import (
    monitor_teams,
    team_freshness_days,
    team_expired_metric,
    key_spend_percentage,
    team_total_spend,
    team_monitoring_failed_metric,
    active_team_labels,
    reconcile_team_keys,
    RegionKeyStateCache,
    _resolve_key_state,
    _get_snapshot_remaining_cents,
)
from app.core.team_service import get_team_keys_by_region
from app.schemas.limits import (
    ResourceType,
    UnitType,
    OwnerType,
    LimitSource,
    LimitType,
)
from unittest.mock import AsyncMock, patch, Mock

# Values reconcile_team_keys is driven with in these tests
RENEWAL_PERIOD_DAYS = 30
MAX_BUDGET_PER_KEY = 50.0


def test_get_snapshot_remaining_cents_scopes_to_current_period(
    db, test_team, test_region
):
    now = datetime.now(UTC)
    period_start = now - timedelta(days=1)
    period_end = now + timedelta(days=30)
    previous_start = period_start - timedelta(days=31)
    previous_end = period_start

    db.add_all(
        [
            DBPeriodicBudgetLedgerEntry(
                team_id=test_team.id,
                region_id=test_region.id,
                entry_type="subscription",
                amount_cents=100,
                consumed_cents=40,
                purchased_at=period_start,
                effective_period_start=period_start,
                effective_period_end=period_end,
                expires_at=period_end,
                is_active=True,
            ),
            DBPeriodicBudgetLedgerEntry(
                team_id=test_team.id,
                region_id=test_region.id,
                entry_type="subscription",
                amount_cents=100,
                consumed_cents=0,
                purchased_at=previous_start,
                effective_period_start=previous_start,
                effective_period_end=previous_end,
                expires_at=period_end,
                is_active=True,
            ),
            DBPeriodicBudgetLedgerEntry(
                team_id=test_team.id,
                region_id=test_region.id,
                entry_type="topup",
                amount_cents=50,
                consumed_cents=10,
                purchased_at=period_start + timedelta(hours=1),
                expires_at=period_end,
                is_active=True,
            ),
            DBPeriodicBudgetLedgerEntry(
                team_id=test_team.id,
                region_id=test_region.id,
                entry_type="topup_rollover",
                amount_cents=30,
                consumed_cents=5,
                purchased_at=period_start + timedelta(hours=2),
                expires_at=period_end,
                is_active=True,
            ),
            DBPeriodicBudgetLedgerEntry(
                team_id=test_team.id,
                region_id=test_region.id,
                entry_type="topup",
                amount_cents=70,
                consumed_cents=20,
                purchased_at=previous_start + timedelta(days=5),
                expires_at=period_end,
                is_active=True,
            ),
        ]
    )
    db.commit()

    subscription_remaining_cents, topup_remaining_cents, desired_remaining_cents = (
        _get_snapshot_remaining_cents(
            db=db,
            team_id=test_team.id,
            region_id=test_region.id,
            period_start=period_start,
            period_end=period_end,
        )
    )

    assert subscription_remaining_cents == 60
    assert topup_remaining_cents == 65
    assert desired_remaining_cents == 125


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_calls_limit_service(
    mock_litellm, mock_ses, mock_limit_service, db, test_team
):
    """
    Test that monitor_teams calls the limit service to set team limits.

    GIVEN: A team exists in the database
    WHEN: The monitor_teams function runs
    THEN: The limit service is called to set team limits
    """
    # Setup test data
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)  # 15 days old
    db.add(test_team)
    db.commit()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify limit service was called with the correct team
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_basic_metrics(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_team,
):
    """
    Test basic team monitoring metrics for teams with and without a payment.
    """
    # Setup test data
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)  # 15 days old
    db.add(test_team)
    db.commit()

    # Create a second team with a payment
    team_with_payment = DBTeam(
        name="Team With Payment",
        stripe_customer_id="cus_456",
        created_at=datetime.now(UTC) - timedelta(days=20),  # 20 days old
        last_payment=datetime.now(UTC) - timedelta(days=10),  # Last payment 10 days ago
    )
    db.add(team_with_payment)
    db.commit()

    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={"info": {"spend": 0, "max_budget": 100, "key_alias": "test"}}
    )

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify metrics for team without payment (age since creation)
    assert (
        team_freshness_days.labels(
            team_id=str(test_team.id), team_name=test_team.name
        )._value.get()
        == 15
    )

    # Verify metrics for team with payment (age since last payment)
    assert (
        team_freshness_days.labels(
            team_id=str(team_with_payment.id), team_name=team_with_payment.name
        )._value.get()
        == 10
    )

    # Verify limit service was called for both teams
    # Once per team in set_team_and_user_limits, then once per team in reconcile_team_keys
    assert (
        mock_limit_service.call_count == 4
    )  # 1 at start + 2 teams (set_team_and_user_limits) + 2 teams (reconcile_team_keys)
    mock_limit_instance.set_team_limits.assert_called()


@pytest.mark.parametrize(
    "team_age,expected_days_remaining,template_name",
    [
        (23, 7, "team-expiring"),
        (25, 5, "team-expiring"),
        (30, 0, "trial-expired"),
    ],
)
@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_notification_scenarios(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    team_age,
    expected_days_remaining,
    template_name,
    db,
    test_team,
    test_team_admin,
):
    """
    Test notification scenarios for teams approaching or reaching expiration.

    GIVEN: A team approaching or at expiration with different ages
    WHEN: The monitoring workflow runs
    THEN: Appropriate notifications are sent with correct template and days remaining
    """
    # Setup test team with specified age
    test_team.created_at = datetime.now(UTC) - timedelta(days=team_age)
    test_team.admin_email = test_team_admin.email
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify email was sent
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args["to_addresses"] == [test_team.admin_email]
    assert call_args["template_name"] == template_name
    assert call_args["template_data"]["name"] == test_team.name

    # For trial-expired template, there's no days_remaining field
    if template_name == "team-expiring":
        assert call_args["template_data"]["days_remaining"] == expected_days_remaining

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_key_expiration(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_team,
    test_region,
    test_team_key_creator,
):
    """
    Test key expiration for expired teams.
    """
    # Setup expired test team (31 days old)
    test_team.created_at = datetime.now(UTC) - timedelta(days=31)
    db.add(test_team)
    db.commit()

    # Setup test key
    test_key = DBPrivateAIKey(
        name="Test Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        owner_id=test_team_key_creator.id,
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test_token",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 40.0, "max_budget": 50.0, "key_alias": "test-key"}
        }
    )
    mock_litellm_instance.update_key_duration = AsyncMock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify key was expired
    mock_litellm_instance.update_key_duration.assert_called_once_with(
        "test_token", "0d"
    )

    # Verify expired metric was incremented
    assert (
        team_expired_metric.labels(
            team_id=str(test_team.id), team_name=test_team.name
        )._value.get()
        == 1
    )

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_pool_team_with_purchase_not_expired(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_team,
    test_region,
    test_team_key_creator,
):
    """
    Test that pool teams with purchases are NOT treated as expired trials.

    Regression test: pool teams had their keys expired by monitor_teams because
    The expire_keys logic only checked days_remaining (past 30-day trial),
    incorrectly treating paying pool teams as expired trials.
    """
    # Setup: pool team, 31 days old (past trial), but with a pool purchase
    test_team.created_at = datetime.now(UTC) - timedelta(days=31)
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    test_team.last_pool_purchase = datetime.now(UTC) - timedelta(days=1)
    db.add(test_team)
    db.commit()

    # Add a pool purchase record
    pool_purchase = DBPoolPurchase(
        team_id=test_team.id,
        region_id=test_region.id,
        amount_cents=2500,
        currency="usd",
        purchased_at=datetime.now(UTC) - timedelta(days=1),
        stripe_payment_id="cs_live_test_pool_not_expired",
    )
    db.add(pool_purchase)
    db.commit()

    # Setup test key
    test_key = DBPrivateAIKey(
        name="Pool Team Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        owner_id=test_team_key_creator.id,
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="pool_test_token",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 10.0, "max_budget": 50.0, "key_alias": "pool-key"}
        }
    )
    mock_litellm_instance.update_key_duration = AsyncMock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Key should NOT have been expired
    mock_litellm_instance.update_key_duration.assert_not_called()


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_pool_team_without_purchase_not_expired(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_team,
    test_region,
    test_team_key_creator,
):
    """
    Test that pool teams WITHOUT purchases are NOT expired as trials.
    """
    # Setup: pool team, 31 days old (past trial), no purchases
    test_team.created_at = datetime.now(UTC) - timedelta(days=31)
    test_team.budget_type = BudgetType.POOL
    test_team.require_purchase_for_requests = True
    db.add(test_team)
    db.commit()

    # Setup test key
    test_key = DBPrivateAIKey(
        name="Pool Team Key No Purchase",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        owner_id=test_team_key_creator.id,
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="pool_no_purchase_token",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 10.0, "max_budget": 50.0, "key_alias": "pool-key-np"}
        }
    )
    mock_litellm_instance.update_key_duration = AsyncMock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Key should NOT have been expired
    mock_litellm_instance.update_key_duration.assert_not_called()


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_key_spend(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_team,
    test_region,
    test_team_key_creator,
):
    """
    Test key spend monitoring and metrics.
    """
    # Setup test key
    test_key = DBPrivateAIKey(
        name="Test Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        owner_id=test_team_key_creator.id,
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test_token",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 40.0, "max_budget": 50.0, "key_alias": "test-key"}
        }
    )

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify key spend metrics
    assert (
        key_spend_percentage.labels(
            team_id=str(test_team.id), team_name=test_team.name, key_alias="test-key"
        )._value.get()
        == 80.0
    )  # 40/50 * 100

    # Verify team total spend
    assert (
        team_total_spend.labels(
            team_id=str(test_team.id), team_name=test_team.name
        )._value.get()
        == 40.0
    )

    # Verify key was not expired (team is not expired)
    mock_litellm_instance.update_key_duration.assert_not_called()

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
async def test_monitor_teams_active_labels(
    mock_litellm, mock_ses, mock_limit_service, db, test_team
):
    """
    Test handling of active team labels.
    """
    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # First run with test team
    await monitor_teams(db)

    # Verify test team is tracked
    assert (str(test_team.id), test_team.name) in active_team_labels

    # Remove test team
    db.delete(test_team)
    db.commit()

    # Run monitoring again
    await monitor_teams(db)

    # Verify test team metrics are zeroed out
    assert (
        team_freshness_days.labels(
            team_id=str(test_team.id), team_name=test_team.name
        )._value.get()
        == 0
    )

    # Verify test team is no longer in active labels
    assert (str(test_team.id), test_team.name) not in active_team_labels

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called()


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
async def test_monitor_teams_error_handling(
    mock_litellm, mock_ses, mock_limit_service, db, test_team, test_region
):
    """
    Test error handling in team monitoring.
    """
    # Setup test key with invalid token
    test_key = DBPrivateAIKey(
        name="Test Key",
        database_name="test_db",
        database_username="test_user",
        database_password="test_pass",
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="invalid_token",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service to raise error
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(side_effect=Exception("API Error"))

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring - should not raise exception
    await monitor_teams(db)

    # Verify team metrics are still set
    assert (
        team_freshness_days.labels(
            team_id=str(test_team.id), team_name=test_team.name
        )._value.get()
        is not None
    )

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_last_monitored_recently(
    mock_litellm, mock_ses, mock_limit_service, db, test_team, test_team_admin
):
    """
    Test that notifications are not sent when team was monitored recently (within 24 hours).
    """
    # Setup test team approaching expiration (23 days old, 7 days remaining)
    test_team.created_at = datetime.now(UTC) - timedelta(days=23)
    test_team.admin_email = test_team_admin.email
    # Set last_monitored to 12 hours ago (within 24-hour window)
    expected_last_monitored = datetime.now(UTC) - timedelta(hours=12)
    test_team.last_monitored = expected_last_monitored
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify no email was sent (team was recently monitored)
    mock_ses_instance.send_email.assert_not_called()

    # Verify last_monitored was not updated (since no notifications were sent)
    db.refresh(test_team)
    # Use approximate comparison due to timestamp precision differences
    assert abs((test_team.last_monitored - expected_last_monitored).total_seconds()) < 1

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_last_monitored_old(
    mock_litellm, mock_ses, mock_limit_service, db, test_team, test_team_admin
):
    """
    Test that notifications are sent when team was last monitored more than 24 hours ago.
    """
    # Setup test team approaching expiration (23 days old, 7 days remaining)
    test_team.created_at = datetime.now(UTC) - timedelta(days=23)
    test_team.admin_email = test_team_admin.email
    # Set last_monitored to 25 hours ago (outside 24-hour window)
    old_last_monitored = datetime.now(UTC) - timedelta(hours=25)
    test_team.last_monitored = old_last_monitored
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify email was sent (team was not recently monitored)
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args["to_addresses"] == [test_team.admin_email]
    assert call_args["template_name"] == "team-expiring"
    assert call_args["template_data"]["days_remaining"] == 7

    # Verify last_monitored was updated (since notifications were sent)
    db.refresh(test_team)
    assert test_team.last_monitored is not None
    assert test_team.last_monitored > old_last_monitored

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_last_monitored_none(
    mock_litellm, mock_ses, mock_limit_service, db, test_team, test_team_admin
):
    """
    Test that notifications are sent when team has never been monitored (last_monitored is None).
    """
    # Setup test team approaching expiration (23 days old, 7 days remaining)
    test_team.created_at = datetime.now(UTC) - timedelta(days=23)
    test_team.admin_email = test_team_admin.email
    # Ensure last_monitored is None (never monitored)
    test_team.last_monitored = None
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify email was sent (team was never monitored)
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args["to_addresses"] == [test_team.admin_email]
    assert call_args["template_name"] == "team-expiring"
    assert call_args["template_data"]["days_remaining"] == 7

    # Verify last_monitored was updated (since notifications were sent)
    db.refresh(test_team)
    assert test_team.last_monitored is not None

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_metrics_always_emitted(
    mock_litellm, mock_ses, mock_limit_service, db, test_team
):
    """
    Test that metrics are always emitted regardless of last_monitored status.
    """
    # Setup test team with recent monitoring
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)
    expected_last_monitored = datetime.now(UTC) - timedelta(
        hours=12
    )  # Recently monitored
    test_team.last_monitored = expected_last_monitored
    db.add(test_team)
    db.commit()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify metrics are still emitted even though team was recently monitored
    assert (
        team_freshness_days.labels(
            team_id=str(test_team.id), team_name=test_team.name
        )._value.get()
        == 15
    )

    # Verify last_monitored was not updated (no notifications sent)
    db.refresh(test_team)
    # Use approximate comparison due to timestamp precision differences
    assert abs((test_team.last_monitored - expected_last_monitored).total_seconds()) < 1

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_includes_renewal_period_check(
    mock_litellm, mock_ses, mock_limit_service, db, test_team, test_region
):
    """
    Test that the monitoring workflow includes renewal period checks when conditions are met.

    Given: A team whose keys have passed the renewal period
    When: The monitoring workflow runs
    Then: The reconcile_team_keys function should be called with renewal_period_days
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mocks
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={"info": {"spend": 0, "max_budget": 100, "key_alias": "test"}}
    )

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify that get_key_info was called (indicating the combined function ran)
    # The function should have been called to get key info for monitoring AND renewal period checks
    assert mock_instance.get_key_info.called

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_does_not_include_renewal_period_check_when_not_passed(
    mock_litellm, mock_ses, mock_limit_service, db, test_team, test_region
):
    """
    Test that the monitoring workflow does not include renewal period checks when conditions are not met.

    Given: A team whose renewal period hasn't passed
    When: The monitoring workflow runs
    Then: The reconcile_team_keys function should be called without renewal_period_days
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=15
    )  # 15 days ago (before 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mocks
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={"info": {"spend": 0, "max_budget": 100, "key_alias": "test"}}
    )

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify that get_key_info was called (for monitoring) but no renewal period updates occurred
    # Since renewal period hasn't passed, the function should still be called but without renewal checks
    assert mock_instance.get_key_info.called

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_with_renewal_period_updates(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that reconcile_team_keys updates keys after renewal period when LiteLLM has reset their budget within the last hour.

    Given: A team with keys that have had their budget reset within the last hour
    When: reconcile_team_keys is called with renewal_period_days
    Then: The budget_duration should be updated to match the renewal period
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create keys for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)

    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_456",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)

    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    # Mock key info responses - both keys have different budget amounts, triggering updates
    mock_instance.get_key_info.side_effect = [
        # Team key - different budget amount triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "team_key",
                "spend": 0.0,
                "max_budget": 100.0,  # Different from expected (50.0)
                "budget_duration": "15d",
            }
        },
        # User key - different budget amount triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "user_key",
                "spend": 5.0,
                "max_budget": 25.0,  # Different from expected (50.0)
                "budget_duration": "15d",
            }
        },
    ]

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the combined function with renewal period days and budget amount
    team_total = await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Verify LiteLLM service was initialized correctly
    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # Verify get_key_info was called for both keys
    assert mock_instance.get_key_info.call_count == 2

    # Only the budget amount drifted, so the write must go through
    # update_key_budget, which leaves duration/expiry alone.
    assert mock_instance.update_key_budget.call_count == 2
    mock_instance.update_budget.assert_not_called()

    # Check the first call (team key)
    first_call = mock_instance.update_key_budget.call_args_list[0]
    assert (
        first_call[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert first_call[1]["max_budget"] == MAX_BUDGET_PER_KEY
    # budget_duration is not None and nothing else drifted, so it must not be sent
    assert first_call[1]["budget_duration"] is None

    # Check the second call (user key)
    second_call = mock_instance.update_key_budget.call_args_list[1]
    assert (
        second_call[0][0] == "user_token_456"
    )  # First positional argument should be litellm_token
    assert second_call[1]["max_budget"] == MAX_BUDGET_PER_KEY
    # budget_duration is not None and nothing else drifted, so it must not be sent
    assert second_call[1]["budget_duration"] is None

    # Verify team total spend is calculated correctly
    assert team_total == 5.0  # 0.0 + 5.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_with_renewal_period_updates_no_renewal(
    mock_litellm, db, test_team, test_region, test_team_user, test_team_key_creator
):
    """
    Test that reconcile_team_keys updates budget_duration with no renewal period given.

    Given: A team with keys that have had their budget reset within the last hour
    When: reconcile_team_keys is called with renewal_period_days
    Then: The budget_duration should be updated but budget_amount should not be set
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create keys for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)

    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_456",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)

    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    # Mock key info responses - both keys have None budget_duration, triggering updates
    mock_instance.get_key_info.side_effect = [
        # Team key - None budget_duration triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "team_key",
                "spend": 0.0,
                "max_budget": 100.0,
                "budget_duration": None,  # None triggers update
            }
        },
        # User key - None budget_duration triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "user_key",
                "spend": 5.0,
                "max_budget": 50.0,
                "budget_duration": None,  # None triggers update
            }
        },
    ]

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the combined function with renewal period days (no budget amount)
    team_total = await reconcile_team_keys(
        db, test_team, keys_by_region, False, 30, None
    )  # Use default 30 days, no budget amount

    # Verify LiteLLM service was initialized correctly
    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )

    # Verify get_key_info was called for both keys
    assert mock_instance.get_key_info.call_count == 2

    # Only budget_duration drifted, so the key's expiry must not be touched
    assert mock_instance.update_key_budget.call_count == 2
    mock_instance.update_budget.assert_not_called()

    # Check the first call (team key)
    first_call = mock_instance.update_key_budget.call_args_list[0]
    assert (
        first_call[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert first_call[1]["budget_duration"] == "30d"
    # Should not have a budget amount
    assert first_call[1]["max_budget"] is None

    # Check the second call (user key)
    second_call = mock_instance.update_key_budget.call_args_list[1]
    assert (
        second_call[0][0] == "user_token_456"
    )  # First positional argument should be litellm_token
    assert second_call[1]["budget_duration"] == "30d"
    # Should not have a budget amount
    assert second_call[1]["max_budget"] is None

    # Verify team total spend is calculated correctly
    assert team_total == 5.0  # 0.0 + 5.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_none_budget_duration_handled(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that reconcile_team_keys handles None budget_duration gracefully.

    Given: A team with keys where budget_duration is None
    When: reconcile_team_keys is called with renewal_period_days
    Then: The function should not error and should handle None budget_duration gracefully
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - budget_duration is None, but spend is non-zero
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time + timedelta(days=30)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,  # Non-zero spend
            "max_budget": 100.0,
            "budget_duration": None,  # None budget_duration
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Verify a write was issued because budget_duration is None (forces update)
    assert mock_instance.update_key_budget.call_count == 1
    mock_instance.update_budget.assert_not_called()
    update_call = mock_instance.update_key_budget.call_args
    assert (
        update_call[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert update_call[1]["budget_duration"] == f"{RENEWAL_PERIOD_DAYS}d"
    assert update_call[1]["max_budget"] == MAX_BUDGET_PER_KEY

    # Verify team total spend is calculated correctly
    assert team_total == 10.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_zero_duration_renewal(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that reconcile_team_keys properly renews keys with "0d" duration.

    Given: A key that has been incorrectly set to "0d" duration
    When: reconcile_team_keys is called with renewal_period_days
    Then: The key should be updated to the correct duration
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - key has "0d" duration (expired due to bug)
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (
                current_time - timedelta(days=2)
            ).isoformat(),  # Reset time in the past
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": 100.0,
            "budget_duration": "0d",  # Expired key due to bug
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Verify a write was issued to fix the "0d" duration
    assert mock_instance.update_key_budget.call_count == 1
    mock_instance.update_budget.assert_not_called()
    update_call = mock_instance.update_key_budget.call_args
    assert (
        update_call[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert update_call[1]["budget_duration"] == f"{RENEWAL_PERIOD_DAYS}d"
    assert update_call[1]["max_budget"] == MAX_BUDGET_PER_KEY

    # Verify team total spend is calculated correctly
    assert team_total == 10.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_update_budget_parameter_issue(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that update_budget is called with correct parameters when budget amount needs updating.

    GIVEN: A team with keys that have different budget amounts
    WHEN: reconcile_team_keys is called with renewal period and budget amount
    THEN: the write should be called with litellm_token as first positional argument, not as keyword argument
    """

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    # Mock key info response - different budget amount triggers update
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "key_alias": "test_key",
            "spend": 0.0,
            "max_budget": 27.0,  # Different from expected (120.0)
            "budget_duration": "30d",
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days and budget amount
    await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Only the budget amount drifted, so duration/expiry must be left alone
    assert mock_instance.update_key_budget.call_count == 1
    mock_instance.update_budget.assert_not_called()

    # Check that litellm_token is passed as first positional argument, not as keyword
    call_args = mock_instance.update_key_budget.call_args
    # After the fix, litellm_token should be the first positional argument
    assert (
        call_args[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert (
        call_args[1]["max_budget"] == MAX_BUDGET_PER_KEY
    )  # max_budget as keyword argument
    # A healthy budget_duration must not be sent, since null clears it in LiteLLM
    assert call_args[1]["budget_duration"] is None


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_expiry_within_next_month(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that reconcile_team_keys updates keys that expire within the next month.

    Given: A key that expires within the next 30 days
    When: reconcile_team_keys is called with renewal_period_days
    Then: The key should be updated to the renewal period duration
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    current_time = datetime.now(UTC)
    # Set expiry date to 15 days from now (within the 30-day window)
    expiry_date = current_time + timedelta(days=15)

    # Mock key info response - key expires within next month
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": MAX_BUDGET_PER_KEY,  # Use the same budget amount to avoid Rule 1 trigger
            "budget_duration": "30d",
            "expires": expiry_date.isoformat(),
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Verify update_budget was called to update the duration for expiring key
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert (
        update_call[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert (
        update_call[0][1] == f"{RENEWAL_PERIOD_DAYS}d"
    )  # Second positional argument should be budget_duration
    # When updating for expiry reasons, budget_amount should be None since we're only updating duration
    assert update_call[1]["budget_amount"] is None

    # Verify team total spend is calculated correctly
    assert team_total == 10.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_expired_key(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that reconcile_team_keys updates keys that are already expired.

    Given: A key that has already expired
    When: reconcile_team_keys is called with renewal_period_days
    Then: The key should be updated to the renewal period duration
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    current_time = datetime.now(UTC)
    # Set expiry date to 5 days ago (already expired)
    expiry_date = current_time - timedelta(days=5)

    # Mock key info response - key is already expired
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": MAX_BUDGET_PER_KEY,  # Use the same budget amount to avoid Rule 1 trigger
            "budget_duration": "30d",
            "expires": expiry_date.isoformat(),
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Verify update_budget was called to update the duration for expired key
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert (
        update_call[0][0] == "team_token_123"
    )  # First positional argument should be litellm_token
    assert (
        update_call[0][1] == f"{RENEWAL_PERIOD_DAYS}d"
    )  # Second positional argument should be budget_duration
    # When updating for expiry reasons, budget_amount should be None since we're only updating duration
    assert update_call[1]["budget_amount"] is None

    # Verify team total spend is calculated correctly
    assert team_total == 10.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_expiry_beyond_next_month(
    mock_litellm,
    db,
    test_team,
    test_region,
    test_team_user,
    test_team_key_creator,
):
    """
    Test that reconcile_team_keys does not update keys that expire beyond the next month.

    Given: A key that expires beyond the next 30 days
    When: reconcile_team_keys is called with renewal_period_days
    Then: The key should not be updated for expiry reasons
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(
        days=35
    )  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()

    current_time = datetime.now(UTC)
    # Set expiry date to 45 days from now (beyond the 30-day window)
    expiry_date = current_time + timedelta(days=45)

    # Mock key info response - key expires beyond next month
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": MAX_BUDGET_PER_KEY,  # Use the same budget amount to avoid Rule 1 trigger
            "budget_duration": "30d",
            "expires": expiry_date.isoformat(),
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await reconcile_team_keys(
        db,
        test_team,
        keys_by_region,
        False,
        RENEWAL_PERIOD_DAYS,
        MAX_BUDGET_PER_KEY,
    )

    # Verify no write was issued for expiry reasons, on either path
    assert mock_instance.update_budget.call_count == 0
    assert mock_instance.update_key_budget.call_count == 0

    # Verify team total spend is calculated correctly
    assert team_total == 10.0


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.SESService")
async def test_monitor_teams_populates_team_metrics(
    mock_ses, mock_litellm_class, mock_limit_service, db, test_team, test_region
):
    """
    Test that monitor_teams function populates DBTeamMetrics table.

    GIVEN: A team with AI keys and regions
    WHEN: monitor_teams is called
    THEN: DBTeamMetrics record is created/updated with spend data
    """
    # Arrange
    # Create a test key for the team
    test_key = DBPrivateAIKey(
        name="test-key",
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test-token-123",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Mock LiteLLM service responses
    mock_litellm_service = AsyncMock()
    mock_litellm_class.return_value = mock_litellm_service
    mock_litellm_service.get_key_info.return_value = {
        "info": {"spend": 75.50, "max_budget": 100.0, "key_alias": "test-key"}
    }

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Act
    await monitor_teams(db)

    # Assert
    metrics = (
        db.query(DBTeamMetrics).filter(DBTeamMetrics.team_id == test_team.id).first()
    )
    assert metrics is not None
    assert metrics.total_spend == 75.50
    assert test_region.name in metrics.regions
    assert metrics.last_spend_calculation is not None

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.SESService")
async def test_monitor_teams_sql_count_error(
    mock_ses, mock_litellm_class, mock_limit_service, db, test_team
):
    """
    Test that monitor_teams handles SQL count queries correctly without throwing SQL expression errors.

    GIVEN: A team with no admin user and no keys
    WHEN: The monitor_teams function runs and tries to count users and keys
    THEN: The function should not throw a SQL expression error
    """
    # Setup test data - team with no admin user (like "Test Team 2 - Always Free")
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)
    db.add(test_team)
    db.commit()

    # Mock limit service to return limits that need counting
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Create mock limits that will trigger the count queries
    from app.schemas.limits import (
        LimitedResource,
        ResourceType,
        UnitType,
        LimitType,
        OwnerType,
        LimitSource,
    )

    mock_limits = [
        LimitedResource(
            id=1,
            limit_type=LimitType.CONTROL_PLANE,
            resource=ResourceType.USER,
            unit=UnitType.COUNT,
            max_value=10.0,
            current_value=0.0,
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            limited_by=LimitSource.DEFAULT,
            created_at=datetime.now(UTC),
        ),
        LimitedResource(
            id=2,
            limit_type=LimitType.CONTROL_PLANE,
            resource=ResourceType.SERVICE_KEY,
            unit=UnitType.COUNT,
            max_value=5.0,
            current_value=0.0,
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            limited_by=LimitSource.DEFAULT,
            created_at=datetime.now(UTC),
        ),
        LimitedResource(
            id=3,
            limit_type=LimitType.CONTROL_PLANE,
            resource=ResourceType.VECTOR_DB,
            unit=UnitType.COUNT,
            max_value=3.0,
            current_value=0.0,
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            limited_by=LimitSource.DEFAULT,
            created_at=datetime.now(UTC),
        ),
    ]

    mock_limit_instance.get_team_limits.return_value = mock_limits
    mock_limit_instance.set_current_value = Mock()

    # This should not raise a SQL expression error
    await monitor_teams(db)

    # Verify the limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.worker.SESService")
async def test_monitor_teams_updates_existing_metrics(
    mock_ses, mock_litellm_class, mock_limit_service, db, test_team, test_region
):
    """
    Test that monitor_teams updates existing DBTeamMetrics records.

    GIVEN: A team with existing metrics record
    WHEN: monitor_teams is called again
    THEN: The existing metrics record is updated with new data
    """
    # Arrange
    # Create existing metrics with a fixed old timestamp
    old_timestamp = datetime.now(UTC) - timedelta(hours=1)
    existing_metrics = DBTeamMetrics(
        team_id=test_team.id,
        total_spend=50.0,
        last_spend_calculation=old_timestamp,
        regions=["old-region"],
        last_updated=old_timestamp,
    )
    db.add(existing_metrics)
    db.commit()
    old_update_date = existing_metrics.last_updated

    # Create a test key
    test_key = DBPrivateAIKey(
        name="test-key",
        team_id=test_team.id,
        region_id=test_region.id,
        litellm_token="test-token-123",
        created_at=datetime.now(UTC),
    )
    db.add(test_key)
    db.commit()

    # Mock LiteLLM service responses
    mock_litellm_service = AsyncMock()
    mock_litellm_class.return_value = mock_litellm_service
    mock_litellm_service.get_key_info.return_value = {
        "info": {"spend": 125.75, "max_budget": 200.0, "key_alias": "test-key"}
    }

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Act
    await monitor_teams(db)

    # Assert
    updated_metrics = (
        db.query(DBTeamMetrics).filter(DBTeamMetrics.team_id == test_team.id).first()
    )
    assert updated_metrics is not None
    assert updated_metrics.total_spend == 125.75
    assert test_region.name in updated_metrics.regions
    assert updated_metrics.last_updated > old_update_date

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_updates_user_budget_limit(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A team with user-owned keys that have accumulated spend
    When: reconcile_team_keys is called
    Then: User's BUDGET limit current_value is updated with their total spend via set_current_value
    """
    from app.db.models import DBLimitedResource
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create user budget limit
    user_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user_budget_limit)

    # Create user-owned key
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 45.50, "max_budget": 100.0, "key_alias": "user_key"}
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify user budget limit was updated
    db.refresh(user_budget_limit)
    assert user_budget_limit.current_value == 45.50


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_updates_service_key_budget_limit(
    mock_litellm, db, test_team, test_region
):
    """
    Given: A team with service keys (no owner_id) that have accumulated spend
    When: reconcile_team_keys is called
    Then: Team's BUDGET limit current_value is updated with service key total spend via set_current_value
    """
    from app.db.models import DBLimitedResource
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create team budget limit
    team_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=200.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(team_budget_limit)

    # Create service key (no owner_id)
    service_key = DBPrivateAIKey(
        name="Service Key",
        litellm_token="service_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(service_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 75.25, "max_budget": 200.0, "key_alias": "service_key"}
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify team budget limit was updated
    db.refresh(team_budget_limit)
    assert team_budget_limit.current_value == 75.25


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_handles_multiple_users_with_varying_spend(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A team with multiple users, each with keys showing different spend amounts
    When: reconcile_team_keys is called
    Then: Each user's BUDGET limit is updated with their individual total spend
    """
    from app.db.models import DBLimitedResource, DBUser
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create second user
    second_user = DBUser(
        email="second@test.com",
        team_id=test_team.id,
        role="member",
        created_at=datetime.now(UTC),
    )
    db.add(second_user)
    db.commit()

    # Create budget limits for both users
    user1_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    user2_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=second_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user1_budget_limit)
    db.add(user2_budget_limit)

    # Create keys for both users
    user1_key = DBPrivateAIKey(
        name="User 1 Key",
        litellm_token="user1_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    user2_key = DBPrivateAIKey(
        name="User 2 Key",
        litellm_token="user2_token_456",
        region=test_region,
        owner_id=second_user.id,
    )
    db.add(user1_key)
    db.add(user2_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        side_effect=[
            {"info": {"spend": 30.0, "max_budget": 100.0, "key_alias": "user1_key"}},
            {"info": {"spend": 50.5, "max_budget": 100.0, "key_alias": "user2_key"}},
        ]
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify each user's budget limit was updated correctly
    db.refresh(user1_budget_limit)
    db.refresh(user2_budget_limit)
    assert user1_budget_limit.current_value == 30.0
    assert user2_budget_limit.current_value == 50.5


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_separates_user_and_service_key_spend(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A team with both user-owned keys (5.0 spend) and service keys (10.0 spend)
    When: reconcile_team_keys is called
    Then: User limits show 5.0 and team limit shows 10.0 separately
    """
    from app.db.models import DBLimitedResource
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create budget limits
    user_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    team_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=200.0,
        current_value=0.0,
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user_budget_limit)
    db.add(team_budget_limit)

    # Create keys
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    service_key = DBPrivateAIKey(
        name="Service Key",
        litellm_token="service_token_456",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(user_key)
    db.add(service_key)
    db.commit()

    # Setup mock LiteLLM service with a function that returns different values based on token
    mock_instance = mock_litellm.return_value

    async def mock_get_key_info(token):
        if token == "service_token_456":
            return {
                "info": {"spend": 10.0, "max_budget": 200.0, "key_alias": "service_key"}
            }
        else:  # user_token_123
            return {
                "info": {"spend": 5.0, "max_budget": 100.0, "key_alias": "user_key"}
            }

    mock_instance.get_key_info = mock_get_key_info

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify separation of spend
    db.refresh(user_budget_limit)
    db.refresh(team_budget_limit)
    assert user_budget_limit.current_value == 5.0
    assert team_budget_limit.current_value == 10.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_handles_missing_user_budget_limit(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A team with user keys but user has no BUDGET limit in database
    When: reconcile_team_keys is called
    Then: Operation continues without error
    """
    # Create user-owned key without creating budget limit
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 25.0, "max_budget": 100.0, "key_alias": "user_key"}
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call should not raise error even without budget limit
    await reconcile_team_keys(db, test_team, keys_by_region, False)


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_handles_missing_team_budget_limit(
    mock_litellm, db, test_team, test_region
):
    """
    Given: A team with service keys but team has no BUDGET limit in database
    When: reconcile_team_keys is called
    Then: Operation continues without error
    """
    # Create service key without creating budget limit
    service_key = DBPrivateAIKey(
        name="Service Key",
        litellm_token="service_token_123",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(service_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 50.0, "max_budget": 200.0, "key_alias": "service_key"}
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call should not raise error even without budget limit
    await reconcile_team_keys(db, test_team, keys_by_region, False)


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_accumulates_spend_for_multiple_user_keys(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A user with 3 keys showing spend of 5.0, 10.0, and 3.5
    When: reconcile_team_keys is called
    Then: User's BUDGET limit current_value is set to 18.5
    """
    from app.db.models import DBLimitedResource
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create user budget limit
    user_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user_budget_limit)

    # Create three keys for the user
    key1 = DBPrivateAIKey(
        name="User Key 1",
        litellm_token="user_token_1",
        region=test_region,
        owner_id=test_team_user.id,
    )
    key2 = DBPrivateAIKey(
        name="User Key 2",
        litellm_token="user_token_2",
        region=test_region,
        owner_id=test_team_user.id,
    )
    key3 = DBPrivateAIKey(
        name="User Key 3",
        litellm_token="user_token_3",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(key1)
    db.add(key2)
    db.add(key3)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        side_effect=[
            {"info": {"spend": 5.0, "max_budget": 100.0, "key_alias": "key1"}},
            {"info": {"spend": 10.0, "max_budget": 100.0, "key_alias": "key2"}},
            {"info": {"spend": 3.5, "max_budget": 100.0, "key_alias": "key3"}},
        ]
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify accumulated spend
    db.refresh(user_budget_limit)
    assert user_budget_limit.current_value == 18.5


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_handles_zero_spend(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A team with keys that have 0.0 spend
    When: reconcile_team_keys is called
    Then: BUDGET limits are updated to 0.0 without error
    """
    from app.db.models import DBLimitedResource
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create budget limits
    user_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=50.0,  # Start with non-zero
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user_budget_limit)

    # Create user key
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)
    db.commit()

    # Setup mock LiteLLM service with zero spend
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 0.0, "max_budget": 100.0, "key_alias": "user_key"}
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify zero spend was set
    db.refresh(user_budget_limit)
    assert user_budget_limit.current_value == 0.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_handles_none_spend_from_litellm(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: Keys where LiteLLM returns None for spend
    When: reconcile_team_keys is called
    Then: Spend is treated as 0.0 and limits are updated correctly
    """
    from app.db.models import DBLimitedResource
    from app.schemas.limits import (
        ResourceType,
        UnitType,
        OwnerType,
        LimitSource,
        LimitType,
    )

    # Create user budget limit
    user_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=25.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user_budget_limit)

    # Create user key
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)
    db.commit()

    # Setup mock LiteLLM service with None spend
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {
                "spend": None,  # LiteLLM returns None
                "max_budget": 100.0,
                "key_alias": "user_key",
            }
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call reconcile_team_keys
    await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify None was treated as 0.0
    db.refresh(user_budget_limit)
    assert user_budget_limit.current_value == 0.0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_defaultdict_initialization(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: The reconcile_team_keys function with total_by_user defaultdict
    When: Accumulating spend for a new user_id
    Then: defaultdict properly initializes without KeyError or TypeError
    """

    # Create user budget limit
    user_budget_limit = DBLimitedResource(
        limit_type=LimitType.DATA_PLANE,
        resource=ResourceType.BUDGET,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        current_value=0.0,
        owner_type=OwnerType.USER,
        owner_id=test_team_user.id,
        limited_by=LimitSource.PRODUCT,
        created_at=datetime.now(UTC),
    )
    db.add(user_budget_limit)

    # Create user key
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_123",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add(user_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 15.0, "max_budget": 100.0, "key_alias": "user_key"}
        }
    )

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # This should not raise KeyError when accessing new user_id
    team_total = await reconcile_team_keys(db, test_team, keys_by_region, False)

    # Verify it worked correctly
    assert team_total == 15.0
    db.refresh(user_budget_limit)
    assert user_budget_limit.current_value == 15.0


@pytest.mark.asyncio
@patch("app.core.worker._check_team_retention_policy", new_callable=AsyncMock)
@patch("app.core.worker.SESService")
@patch("app.core.worker.reconcile_team_keys", new_callable=AsyncMock)
async def test_monitor_teams_handles_individual_team_errors_gracefully(
    mock_reconcile,
    mock_ses,
    mock_retention,
    db,
    test_team,
):
    """
    Test that monitor_teams handles individual team errors gracefully and continues processing.

    GIVEN: Multiple teams where one fails to process
    WHEN: monitor_teams is called
    THEN: The failing team should be logged and skipped, but other teams should continue processing
    """
    # Create a second team
    second_team = DBTeam(name="Second Team", admin_email="second@example.com")
    db.add(second_team)
    db.commit()

    mock_reconcile.return_value = 0.0
    mock_ses.return_value = None

    # The retention check is the first per-team step, so a failure there aborts
    # that team before reconcile_team_keys runs.
    mock_retention.side_effect = [Exception("Test error for team 1"), None]

    # Act
    await monitor_teams(db)

    # Assert
    # Both teams should have been processed (first one failed, second succeeded)
    assert mock_retention.call_count == 2
    # The reconcile_team_keys should have been called for the second team only
    assert mock_reconcile.call_count == 1


@pytest.mark.asyncio
@patch("app.core.worker._check_team_retention_policy", new_callable=AsyncMock)
@patch("app.core.worker.SESService")
@patch("app.core.worker.reconcile_team_keys", new_callable=AsyncMock)
async def test_monitor_teams_records_failure_metric_on_error(
    mock_reconcile,
    mock_ses,
    mock_retention,
    db,
    test_team,
):
    """
    Test that monitor_teams records a failure metric when a team fails to process.

    GIVEN: A team that fails to process
    WHEN: monitor_teams is called
    THEN: A failure metric should be recorded for that team
    """
    mock_reconcile.return_value = 0.0
    mock_ses.return_value = None
    mock_retention.side_effect = Exception("Test error")

    # The metric is a process-wide counter, so compare against its current value.
    metric = team_monitoring_failed_metric.labels(
        team_id=str(test_team.id),
        team_name=test_team.name,
        error_type="Exception",
    )
    before = metric._value.get()

    # Act
    await monitor_teams(db)

    # Assert
    assert mock_retention.call_count == 1
    assert metric._value.get() == before + 1


@pytest.mark.asyncio
@patch("app.core.worker._check_team_retention_policy", new_callable=AsyncMock)
@patch("app.core.worker.SESService")
@patch("app.core.worker.reconcile_team_keys", new_callable=AsyncMock)
async def test_monitor_teams_continues_processing_after_error(
    mock_reconcile,
    mock_ses,
    mock_retention,
    db,
    test_team,
):
    """
    Test that monitor_teams continues processing other teams after one fails.

    GIVEN: Multiple teams where one fails
    WHEN: monitor_teams is called
    THEN: All teams should be attempted, and successful ones should complete processing
    """
    # Create additional teams
    teams = []
    for team_index in range(3):
        team = DBTeam(
            name=f"Team {team_index + 2}",
            admin_email=f"team{team_index + 2}@example.com",
        )
        db.add(team)
        teams.append(team)
    db.commit()

    mock_reconcile.return_value = 0.0
    mock_ses.return_value = None

    # Fail the retention check of the second team only
    def side_effect(*args, **kwargs):
        team = args[1]  # Second argument is the team
        if team.id == teams[1].id:
            raise Exception("Test error for second team")
        return None

    mock_retention.side_effect = side_effect

    # Act
    await monitor_teams(db)

    # Assert
    # All teams should have been processed
    assert mock_retention.call_count == 4  # test_team + 3 new teams
    # reconcile_team_keys should have been called for all teams except the failing one
    assert mock_reconcile.call_count == 3  # 4 teams - 1 failing = 3 successful


# ---------------------------------------------------------------------------
# Bulk /key/list snapshot behaviour
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_region_key_state_cache_lists_region_once(test_region):
    """The snapshot is fetched once per region and reused for later teams."""
    service = AsyncMock()
    service.list_all_keys.return_value = {"hash-1": {"spend": 1.0}}

    cache = RegionKeyStateCache()
    first = await cache.get(test_region, service)
    second = await cache.get(test_region, service)

    assert first == {"hash-1": {"spend": 1.0}}
    assert second is first
    # Reused, not re-listed: this is what keeps ~1.1k teams from re-paginating
    assert service.list_all_keys.call_count == 1


@pytest.mark.asyncio
async def test_region_key_state_cache_caches_failure(test_region):
    """A failing region degrades to an empty snapshot and is not retried per team."""
    service = AsyncMock()
    service.list_all_keys.side_effect = Exception("litellm down")

    cache = RegionKeyStateCache()
    first = await cache.get(test_region, service)
    second = await cache.get(test_region, service)

    assert first == {}
    assert second == {}
    assert service.list_all_keys.call_count == 1


@pytest.mark.asyncio
async def test_region_key_state_cache_rejects_non_dict(test_region):
    """A non-mapping response degrades instead of being indexed into."""
    service = AsyncMock()
    service.list_all_keys.return_value = ["not", "a", "dict"]

    cache = RegionKeyStateCache()
    result = await cache.get(test_region, service)

    assert result == {}


class FakeClock:
    """Hand-advanced monotonic clock, so TTL tests need no sleeping."""

    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.mark.asyncio
async def test_region_key_state_cache_reuses_within_ttl(test_region):
    """A snapshot younger than the TTL is reused, not re-listed."""
    service = AsyncMock()
    service.list_all_keys.return_value = {"hash-1": {"spend": 1.0}}
    clock = FakeClock()

    cache = RegionKeyStateCache(ttl_seconds=600, clock=clock)
    await cache.get(test_region, service)
    clock.advance(599)
    await cache.get(test_region, service)

    assert service.list_all_keys.call_count == 1


@pytest.mark.asyncio
async def test_region_key_state_cache_relists_after_ttl(test_region):
    """Past the TTL the region is listed again, so spend cannot go stale silently."""
    service = AsyncMock()
    service.list_all_keys.side_effect = [
        {"hash-1": {"spend": 1.0}},
        {"hash-1": {"spend": 7.0}},
    ]
    clock = FakeClock()

    cache = RegionKeyStateCache(ttl_seconds=600, clock=clock)
    first = await cache.get(test_region, service)
    clock.advance(601)
    second = await cache.get(test_region, service)

    assert first == {"hash-1": {"spend": 1.0}}
    assert second == {"hash-1": {"spend": 7.0}}
    assert service.list_all_keys.call_count == 2


@pytest.mark.asyncio
async def test_region_key_state_cache_ttl_starts_after_the_listing(test_region):
    """The TTL is stamped when the listing returns, not when it was requested."""
    clock = FakeClock()

    async def slow_list():
        clock.advance(500)
        return {"hash-1": {"spend": 1.0}}

    service = AsyncMock()
    service.list_all_keys.side_effect = slow_list

    cache = RegionKeyStateCache(ttl_seconds=600, clock=clock)
    await cache.get(test_region, service)
    clock.advance(599)
    await cache.get(test_region, service)

    assert service.list_all_keys.call_count == 1


@pytest.mark.asyncio
async def test_region_key_state_cache_retries_failure_after_ttl(test_region):
    """A broken region is retried once per TTL window, not once per team."""
    service = AsyncMock()
    service.list_all_keys.side_effect = [
        Exception("litellm down"),
        {"hash-1": {"spend": 1.0}},
    ]
    clock = FakeClock()

    cache = RegionKeyStateCache(ttl_seconds=600, clock=clock)
    assert await cache.get(test_region, service) == {}
    clock.advance(599)
    assert await cache.get(test_region, service) == {}
    assert service.list_all_keys.call_count == 1

    clock.advance(2)
    assert await cache.get(test_region, service) == {"hash-1": {"spend": 1.0}}
    assert service.list_all_keys.call_count == 2


@pytest.mark.asyncio
async def test_region_key_state_cache_zero_ttl_always_relists(test_region):
    """A zero TTL turns the cache off, for an operator who needs every read fresh."""
    service = AsyncMock()
    service.list_all_keys.return_value = {"hash-1": {"spend": 1.0}}

    cache = RegionKeyStateCache(ttl_seconds=0, clock=FakeClock())
    await cache.get(test_region, service)
    await cache.get(test_region, service)

    assert service.list_all_keys.call_count == 2


@pytest.mark.asyncio
async def test_resolve_key_state_prefers_snapshot(test_region):
    """A key present in the snapshot must not trigger a /key/info call."""
    key = DBPrivateAIKey(name="k", litellm_token="sk-abc", region_id=test_region.id)
    from app.services.litellm import LiteLLMService as _Svc

    snapshot = {_Svc.hash_token("sk-abc"): {"spend": 3.0, "max_budget": 10.0}}
    service = AsyncMock()

    info = await _resolve_key_state(key, test_region, service, snapshot)

    assert info == {"spend": 3.0, "max_budget": 10.0}
    service.get_key_info.assert_not_called()


@pytest.mark.asyncio
async def test_resolve_key_state_falls_back_when_key_missing(test_region):
    """A key created after the snapshot still resolves, via /key/info."""
    key = DBPrivateAIKey(name="k", litellm_token="sk-new", region_id=test_region.id)
    snapshot = {"some-other-hash": {"spend": 1.0}}
    service = AsyncMock()
    service.get_key_info.return_value = {"info": {"spend": 7.0}}

    info = await _resolve_key_state(key, test_region, service, snapshot)

    assert info == {"spend": 7.0}
    service.get_key_info.assert_awaited_once_with("sk-new")


@pytest.mark.asyncio
async def test_resolve_key_state_falls_back_on_empty_snapshot(test_region):
    """An empty snapshot (failed listing) falls back for every key."""
    key = DBPrivateAIKey(name="k", litellm_token="sk-abc", region_id=test_region.id)
    service = AsyncMock()
    service.get_key_info.return_value = {"info": {"spend": 2.0}}

    info = await _resolve_key_state(key, test_region, service, {})

    assert info == {"spend": 2.0}
    service.get_key_info.assert_awaited_once()


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_uses_snapshot_not_per_key_info(
    mock_litellm, db, test_team, test_region, test_team_user
):
    """
    Given: A team whose keys are all present in the bulk snapshot
    When: reconcile_team_keys runs
    Then: Spend comes from the snapshot and no per-key /key/info call is made
    """
    from app.services.litellm import LiteLLMService as _Svc

    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="sk-team-1",
        region=test_region,
        team_id=test_team.id,
    )
    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="sk-user-1",
        region=test_region,
        owner_id=test_team_user.id,
    )
    db.add_all([team_key, user_key])
    db.commit()

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.list_all_keys = AsyncMock(
        return_value={
            _Svc.hash_token("sk-team-1"): {
                "spend": 4.0,
                "max_budget": 50.0,
                "key_alias": "team_key",
                "budget_duration": "30d",
            },
            _Svc.hash_token("sk-user-1"): {
                "spend": 6.0,
                "max_budget": 50.0,
                "key_alias": "user_key",
                "budget_duration": "30d",
            },
        }
    )

    keys_by_region = get_team_keys_by_region(db, test_team.id)
    team_total = await reconcile_team_keys(db, test_team, keys_by_region, False)

    assert team_total == 10.0
    # The whole point of the change: one bulk listing, zero per-key lookups
    assert mock_instance.list_all_keys.await_count == 1
    mock_instance.get_key_info.assert_not_called()


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_shares_cache_across_teams(
    mock_litellm, db, test_team, test_region
):
    """A shared cache means a second team in the same region does not re-list."""
    from app.services.litellm import LiteLLMService as _Svc

    key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="sk-team-1",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(key)

    other_team = DBTeam(name="Other Team", admin_email="other@example.com")
    db.add(other_team)
    db.commit()

    other_key = DBPrivateAIKey(
        name="Other Key",
        litellm_token="sk-other-1",
        region=test_region,
        team_id=other_team.id,
    )
    db.add(other_key)
    db.commit()

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.list_all_keys = AsyncMock(
        return_value={
            _Svc.hash_token("sk-team-1"): {"spend": 1.0, "max_budget": 10.0},
            _Svc.hash_token("sk-other-1"): {"spend": 2.0, "max_budget": 10.0},
        }
    )

    cache = RegionKeyStateCache()
    total_a = await reconcile_team_keys(
        db,
        test_team,
        get_team_keys_by_region(db, test_team.id),
        False,
        key_state_cache=cache,
    )
    total_b = await reconcile_team_keys(
        db,
        other_team,
        get_team_keys_by_region(db, other_team.id),
        False,
        key_state_cache=cache,
    )

    assert total_a == 1.0
    assert total_b == 2.0
    assert mock_instance.list_all_keys.await_count == 1


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_still_writes_from_snapshot_state(
    mock_litellm, db, test_team, test_region
):
    """
    Given: A key whose snapshot budget differs from the expected budget
    When: reconcile_team_keys runs off the snapshot
    Then: The budget correction is still issued (writes are unchanged)
    """
    from app.services.litellm import LiteLLMService as _Svc

    key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="sk-team-1",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.list_all_keys = AsyncMock(
        return_value={
            _Svc.hash_token("sk-team-1"): {
                "spend": 1.0,
                "max_budget": 25.0,  # differs from the 50.0 we pass in
                "key_alias": "team_key",
                "budget_duration": "30d",
            }
        }
    )

    keys_by_region = get_team_keys_by_region(db, test_team.id)
    await reconcile_team_keys(db, test_team, keys_by_region, False, 30, 50.0)

    mock_instance.update_key_budget.assert_awaited_once()
    mock_instance.update_budget.assert_not_awaited()
    args = mock_instance.update_key_budget.await_args
    assert args.args[0] == "sk-team-1"
    assert args.kwargs["max_budget"] == 50.0
    # Only the amount drifted in the snapshot, so the healthy budget_duration
    # must not be sent along - LiteLLM reads a null as "clear it".
    assert args.kwargs["budget_duration"] is None


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_near_expiry_still_resets_duration(
    mock_litellm, db, test_team, test_region
):
    """
    Given: A snapshot key that expires within the next month
    When: reconcile_team_keys runs off the snapshot
    Then: The write goes through update_budget, which resets the key duration -
          the narrow write path must not swallow the expiry extension
    """
    from app.services.litellm import LiteLLMService as _Svc

    key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="sk-team-1",
        region=test_region,
        team_id=test_team.id,
    )
    db.add(key)
    db.commit()

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.list_all_keys = AsyncMock(
        return_value={
            _Svc.hash_token("sk-team-1"): {
                "spend": 1.0,
                "max_budget": 50.0,  # matches, so only the expiry drifts
                "key_alias": "team_key",
                "budget_duration": "30d",
                "expires": (datetime.now(UTC) + timedelta(days=5)).isoformat(),
            }
        }
    )

    keys_by_region = get_team_keys_by_region(db, test_team.id)
    await reconcile_team_keys(db, test_team, keys_by_region, False, 30, 50.0)

    mock_instance.update_budget.assert_awaited_once()
    mock_instance.update_key_budget.assert_not_awaited()
    args = mock_instance.update_budget.await_args
    assert args.args[0] == "sk-team-1"
    assert args.args[1] == "30d"
    # Budget matched, so no amount is sent and the existing one is left in place
    assert args.kwargs["budget_amount"] is None


# ---------------------------------------------------------------------------
# Bounded-concurrency key writes
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_run_key_writes_executes_all_and_returns_zero_failures():
    """Every queued write runs; no failures reported on the happy path."""
    from app.core.worker import _run_key_writes

    called = []

    def _make(i):
        async def _write():
            called.append(i)

        return _write

    pending = [(i, _make(i)) for i in range(25)]
    failures = await _run_key_writes(pending, "test-region")

    assert failures == 0
    assert sorted(called) == list(range(25))


@pytest.mark.asyncio
async def test_run_key_writes_isolates_failures():
    """A failing write is counted but does not stop the others."""
    from app.core.worker import _run_key_writes

    done = []

    def _ok(i):
        async def _write():
            done.append(i)

        return _write

    async def _boom():
        raise RuntimeError("litellm rejected the update")

    pending = [(1, _ok(1)), (2, _boom), (3, _ok(3))]
    failures = await _run_key_writes(pending, "test-region")

    assert failures == 1
    assert sorted(done) == [1, 3]


@pytest.mark.asyncio
async def test_run_key_writes_is_bounded():
    """No more than KEY_WRITE_CONCURRENCY writes are in flight at once."""
    from app.core import worker as worker_module
    from app.core.worker import _run_key_writes

    in_flight = 0
    peak = 0

    def _make():
        async def _write():
            nonlocal in_flight, peak
            in_flight += 1
            peak = max(peak, in_flight)
            await asyncio.sleep(0)
            in_flight -= 1

        return _write

    pending = [(i, _make()) for i in range(50)]
    await _run_key_writes(pending, "test-region")

    assert peak <= worker_module.KEY_WRITE_CONCURRENCY


@pytest.mark.asyncio
async def test_run_key_writes_empty_is_noop():
    """An empty queue short-circuits."""
    from app.core.worker import _run_key_writes

    assert await _run_key_writes([], "test-region") == 0


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_expire_writes_are_batched(
    mock_litellm, db, test_team, test_region
):
    """
    Given: A team with several keys and expire_keys=True
    When: reconcile_team_keys runs
    Then: Every key gets an expiry write, issued through the batched path
    """
    from app.services.litellm import LiteLLMService as _Svc

    tokens = [f"sk-expire-{i}" for i in range(5)]
    for i, tok in enumerate(tokens):
        db.add(
            DBPrivateAIKey(
                name=f"Key {i}",
                litellm_token=tok,
                region=test_region,
                team_id=test_team.id,
            )
        )
    db.commit()

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.update_key_duration = AsyncMock()
    mock_instance.list_all_keys = AsyncMock(
        return_value={
            _Svc.hash_token(tok): {"spend": 1.0, "max_budget": 10.0} for tok in tokens
        }
    )

    keys_by_region = get_team_keys_by_region(db, test_team.id)
    team_total = await reconcile_team_keys(db, test_team, keys_by_region, True)

    # Spend still accumulated for every key
    assert team_total == 5.0
    # One expiry write per key, and no budget writes on the expire path
    assert mock_instance.update_key_duration.await_count == 5
    assert mock_instance.update_budget.await_count == 0
    expired = sorted(
        c.args[0] for c in mock_instance.update_key_duration.await_args_list
    )
    assert expired == sorted(tokens)


@pytest.mark.asyncio
@patch("app.core.worker.KEY_WRITE_BATCH", 2)
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_flushes_writes_before_region_ends(
    mock_litellm, db, test_team, test_region
):
    """
    Given: More keys than KEY_WRITE_BATCH, and no usable bulk snapshot
    When: reconcile_team_keys runs with expire_keys=True
    Then: Expiry writes start landing before the last key has been read

    Without batching, every key stays usable until the whole region is read.
    That wait is worst on the fallback path, where each read is a round-trip.
    """
    tokens = [f"sk-flush-{i}" for i in range(5)]
    for i, tok in enumerate(tokens):
        db.add(
            DBPrivateAIKey(
                name=f"Key {i}",
                litellm_token=tok,
                region=test_region,
                team_id=test_team.id,
            )
        )
    db.commit()

    events = []

    async def _read(token):
        events.append(("read", token))
        return {"info": {"spend": 1.0, "max_budget": 10.0}}

    async def _write(token, duration):
        events.append(("write", token))

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(side_effect=_read)
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.update_key_duration = AsyncMock(side_effect=_write)
    # Empty snapshot forces the per-key fallback read for every key
    mock_instance.list_all_keys = AsyncMock(return_value={})

    keys_by_region = get_team_keys_by_region(db, test_team.id)
    team_total = await reconcile_team_keys(db, test_team, keys_by_region, True)

    assert team_total == 5.0
    # Every key still expired, and spend still counted
    assert mock_instance.update_key_duration.await_count == 5

    kinds = [kind for kind, _ in events]
    first_write = kinds.index("write")
    last_read = len(kinds) - 1 - kinds[::-1].index("read")
    assert first_write < last_read, (
        f"writes only ran after every read finished: {events}"
    )


@pytest.mark.asyncio
@patch("app.core.worker.LiteLLMService")
async def test_reconcile_team_keys_write_failure_does_not_lose_spend(
    mock_litellm, db, test_team, test_region
):
    """A failed expiry write must not stop the other keys being processed."""
    from app.services.litellm import LiteLLMService as _Svc

    tokens = ["sk-a", "sk-b", "sk-c"]
    for i, tok in enumerate(tokens):
        db.add(
            DBPrivateAIKey(
                name=f"Key {i}",
                litellm_token=tok,
                region=test_region,
                team_id=test_team.id,
            )
        )
    db.commit()

    async def _maybe_fail(token, duration):
        if token == "sk-b":
            raise RuntimeError("litellm 500")

    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()
    mock_instance.update_key_budget = AsyncMock()
    mock_instance.update_key_duration = AsyncMock(side_effect=_maybe_fail)
    mock_instance.list_all_keys = AsyncMock(
        return_value={
            _Svc.hash_token(tok): {"spend": 2.0, "max_budget": 10.0} for tok in tokens
        }
    )

    keys_by_region = get_team_keys_by_region(db, test_team.id)
    team_total = await reconcile_team_keys(db, test_team, keys_by_region, True)

    # All three attempted, and spend counted for all three
    assert mock_instance.update_key_duration.await_count == 3
    assert team_total == 6.0


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_does_not_expire_anonymous_trial_team_keys(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_region,
):
    """A trial key minted moments ago must survive monitor_teams.

    The trial team's freshness ran out long ago, so without an exemption every
    trial key is expired on each run.
    """
    from app.core.config import settings
    from app.db.models import DBUser  # noqa: F811

    trial_team = DBTeam(
        name="AI Trial Team",
        admin_email=settings.AI_TRIAL_TEAM_EMAIL,
        is_active=True,
        created_at=datetime.now(UTC) - timedelta(days=200),
    )
    db.add(trial_team)
    db.commit()
    db.refresh(trial_team)

    trial_user = DBUser(
        email="trial-1-abc@example.com",
        team_id=trial_team.id,
        is_active=True,
        role="user",
    )
    db.add(trial_user)
    db.commit()
    db.refresh(trial_user)

    # Minted moments ago — exactly the key the old behaviour destroyed.
    fresh_key = DBPrivateAIKey(
        name="Trial Key for trial-1-abc@example.com",
        database_name="db_trial_1",
        database_username="u_trial_1",
        database_password="pw",
        owner_id=trial_user.id,
        team_id=trial_team.id,
        region_id=test_region.id,
        litellm_token="trial_token_1",
        created_at=datetime.now(UTC),
    )
    db.add(fresh_key)
    db.commit()

    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(
        return_value={
            "info": {"spend": 0.0, "max_budget": 2.0, "key_alias": "trial-key"}
        }
    )
    mock_litellm_instance.update_key_duration = AsyncMock()

    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    await monitor_teams(db)

    mock_litellm_instance.update_key_duration.assert_not_called()


@pytest.mark.asyncio
@patch("app.core.worker.LimitService")
@patch("app.core.worker.SESService")
@patch("app.core.worker.LiteLLMService")
@patch("app.core.config.settings.ENABLE_LIMITS", True)
async def test_monitor_teams_does_not_retire_anonymous_trial_team(
    mock_litellm,
    mock_ses,
    mock_limit_service,
    db,
    test_region,
):
    """A quiet trial team must not be retired for inactivity.

    Retiring it would soft-delete the team that owns every trial key.
    """
    from app.core.config import settings

    trial_team = DBTeam(
        name="AI Trial Team",
        admin_email=settings.AI_TRIAL_TEAM_EMAIL,
        is_active=True,
        created_at=datetime.now(UTC) - timedelta(days=400),
    )
    db.add(trial_team)
    db.commit()
    db.refresh(trial_team)

    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    await monitor_teams(db)

    db.refresh(trial_team)
    assert trial_team.retention_warning_sent_at is None
    assert trial_team.deleted_at is None
