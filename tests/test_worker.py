import pytest
from app.db.models import DBProduct, DBTeamProduct, DBPrivateAIKey, DBTeam, DBTeamMetrics
from datetime import datetime, UTC, timedelta
from app.core.worker import (
    apply_product_for_team,
    remove_product_from_team,
    handle_stripe_event_background,
    monitor_teams,
    team_freshness_days,
    team_expired_metric,
    key_spend_percentage,
    team_total_spend,
    active_team_labels,
    get_team_keys_by_region,
    monitor_team_keys
)
from unittest.mock import AsyncMock, patch, Mock

@pytest.mark.parametrize("event_type,object_type,get_product_func", [
    ("customer.subscription.deleted", "subscription", "get_product_id_from_subscription"),
    ("checkout.session.async_payment_failed", "session", "get_product_id_from_session"),
    ("checkout.session.expired", "session", "get_product_id_from_session"),
    ("customer.subscription.paused", "subscription", "get_product_id_from_subscription"),
])
@pytest.mark.asyncio
async def test_handle_stripe_events_remove_product(event_type, object_type, get_product_func, db, test_team, test_product):
    """
    Test that various Stripe events correctly remove product associations from teams.

    GIVEN: A team with an active product association
    WHEN: A Stripe event occurs that indicates payment/subscription failure
    THEN: The product association is removed from the team
    """
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = event_type

    if object_type == "subscription":
        mock_object = Mock()
        mock_object.customer = "cus_123"
        mock_object.id = "sub_123"
    else:  # session
        mock_object = Mock()
        mock_object.metadata = {"team_id": str(test_team.id)}
        mock_object.customer = "cus_123"
        mock_object.id = "cs_123"

    mock_event.data.object = mock_object

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    with patch(f'app.core.worker.{get_product_func}', new_callable=AsyncMock) as mock_get_product:
        mock_get_product.return_value = test_product.id
        await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with(mock_object.id)
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@pytest.mark.asyncio
async def test_handle_unknown_event_type(db):
    # Arrange
    mock_event = Mock()
    mock_event.type = "unknown.event.type"

    # Act
    await handle_stripe_event_background(mock_event, db)

    # No assertion needed as we're just verifying no error occurs

@pytest.mark.asyncio
async def test_apply_product_success(db, test_team, test_product):
    """
    Test successful application of a product to a team.

    GIVEN: A team and a product exist in the database
    WHEN: The product is applied to the team
    THEN: The team's active products list is updated and last payment date is set
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Apply product to team
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Refresh team from database
    db.refresh(test_team)

    # Verify team was updated correctly
    assert len(test_team.active_products) == 1
    assert test_team.active_products[0].product.id == test_product.id
    assert test_team.last_payment is not None

@pytest.mark.asyncio
async def test_apply_product_team_not_found(db, test_product):
    """
    Test applying a product when team is not found.

    GIVEN: A product exists but team does not
    WHEN: Attempting to apply the product
    THEN: The operation completes without error
    """
    # Try to apply product to non-existent team
    await apply_product_for_team(db, "cus_nonexistent", test_product.id, datetime.now(UTC))
    # No assertions needed as function should complete without error

@pytest.mark.asyncio
async def test_apply_product_product_not_found(db, test_team):
    """
    Test applying a non-existent product to a team.

    GIVEN: A team exists but product does not
    WHEN: Attempting to apply the product
    THEN: The operation completes without error
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Try to apply non-existent product
    await apply_product_for_team(db, test_team.stripe_customer_id, "prod_nonexistent", datetime.now(UTC))
    # No assertions needed as function should complete without error

@pytest.mark.asyncio
async def test_apply_product_multiple_products(db, test_team, test_product):
    """
    Test applying multiple products to a team.

    GIVEN: A team and multiple products exist
    WHEN: Multiple products are applied to the team
    THEN: All products are added to the team's active products list
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Create additional test products
    products = [test_product]  # Start with the fixture product
    for i in range(2):  # Create 2 more products
        product = DBProduct(
            id=f"prod_test{i+1}",
            name=f"Test Product {i+1}",
            user_count=5,
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
        db.add(product)
        products.append(product)
    db.commit()

    # Apply each product to the team
    for product in products:
        await apply_product_for_team(db, test_team.stripe_customer_id, product.id, datetime.now(UTC))

    # Refresh team from database
    db.refresh(test_team)

    # Verify all products were added
    assert len(test_team.active_products) == 3
    product_ids = [team_product.product.id for team_product in test_team.active_products]
    assert all(expected_product.id in product_ids for expected_product in products)

@pytest.mark.asyncio
async def test_apply_product_already_active(db, test_team, test_product):
    """
    Test applying a product that is already active for a team.

    GIVEN: A team has a specific product already active
    WHEN: That product is applied for the team
    THEN: The last payment date is updated, but the list of products is unchanged
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.add(test_team)  # Ensure the team is added to the session
    db.commit()
    db.refresh(test_team)  # Refresh to ensure we have the latest data

    # First apply the product
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Get the initial last payment date
    db.refresh(test_team)
    initial_last_payment = test_team.last_payment

    # Apply the same product again
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Refresh team from database
    db.refresh(test_team)

    # Verify the product list is unchanged
    assert len(test_team.active_products) == 1
    assert test_team.active_products[0].product.id == test_product.id

    # Verify the last payment date was updated
    assert test_team.last_payment > initial_last_payment

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.LiteLLMService')
async def test_apply_product_calls_limit_service(mock_litellm, mock_limit_service, db, test_team, test_product):
    """
    Test that applying a product calls the limit service to set team limits.

    GIVEN: A team and a product exist in the database
    WHEN: The product is applied to the team
    THEN: The limit service is called to set team limits
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()
    mock_limit_instance.get_token_restrictions = Mock(return_value=(30, 50.0, 1000))

    # Apply product to team
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Verify limit service was called with the correct team
    mock_limit_service.assert_called_once_with(db)
    mock_limit_instance.set_team_limits.assert_called_once_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.LiteLLMService')
async def test_apply_product_extends_keys_and_sets_budget(mock_litellm, mock_limit_service, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that applying a product extends keys and sets max budget correctly.

    GIVEN: A team with users and keys (both team-owned and user-owned), and a product which specifies a max_budget of $50 per key
          with a renewal period of 30 days
    WHEN: The product is applied to the team
    THEN: All keys for the team and users in the team are extended and the max_budget is set correctly
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Create test keys for the team
    team_keys = []
    for i in range(2):  # 2 team-owned keys
        key = DBPrivateAIKey(
            name=f"Team Key {i}",
            database_name=f"db_team_{i}",
            database_username="test_user",
            database_password="test_pass",
            team_id=test_team.id,
            region_id=test_region.id,
            litellm_token=f"test_token_team_{i}",
            created_at=datetime.now(UTC)
        )
        db.add(key)
        team_keys.append(key)

    # Create test keys for both team users
    user_keys = []
    for user in [test_team_user, test_team_key_creator]:
        for i in range(2):  # 2 keys per user
            key = DBPrivateAIKey(
                name=f"User Key {i} for {user.email}",
                database_name=f"db_user_{user.id}_{i}",
                database_username="test_user",
                database_password="test_pass",
                owner_id=user.id,
                team_id=test_team.id,
                region_id=test_region.id,
                litellm_token=f"test_token_user_{user.id}_{i}",
                created_at=datetime.now(UTC)
            )
            db.add(key)
            user_keys.append(key)
    db.commit()

    # Setup mock instance
    mock_instance = mock_litellm.return_value
    mock_instance.set_key_restrictions = AsyncMock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()
    mock_limit_instance.get_token_restrictions = Mock(return_value=(30, 50.0, 1000))

    # Apply product to team
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Verify LiteLLM service was initialized with correct region settings
    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # Verify LiteLLM service was called for all keys (both team and user owned)
    all_keys = team_keys + user_keys
    assert mock_instance.set_key_restrictions.call_count == len(all_keys)

    # Verify each key was updated with correct duration and budget
    for key in all_keys:
        # Verify key restrictions update
        restriction_calls = [call for call in mock_instance.set_key_restrictions.call_args_list
                        if call[1]['litellm_token'] == key.litellm_token]
        assert len(restriction_calls) == 1
        assert restriction_calls[0][1]['duration'] == f"{test_product.renewal_period_days}d"
        assert restriction_calls[0][1]['budget_duration'] == f"{test_product.renewal_period_days}d"
        assert restriction_calls[0][1]['budget_amount'] == test_product.max_budget_per_key
        assert restriction_calls[0][1]['rpm_limit'] == test_product.rpm_per_key

    # Verify limit service was called with the correct team
    mock_limit_service.assert_called_once_with(db)
    mock_limit_instance.set_team_limits.assert_called_once_with(test_team)

    # Verify team was updated correctly
    db.refresh(test_team)
    assert len(test_team.active_products) == 1
    assert test_team.active_products[0].product.id == test_product.id
    assert test_team.last_payment is not None

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
async def test_remove_product_calls_limit_service(mock_limit_service, db, test_team, test_product):
    """
    Test that removing a product calls the limit service to set team limits.

    GIVEN: A team with an active product
    WHEN: The product is removed from the team
    THEN: The limit service is called to set team limits
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()
    mock_limit_instance.get_token_restrictions = Mock(return_value=(30, 50.0, 1000))

    # First apply the product to ensure it exists
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Remove the product
    await remove_product_from_team(db, test_team.stripe_customer_id, test_product.id)

    # Verify limit service was called with the correct team
    # It should be called twice: once for apply_product_for_team and once for remove_product_from_team
    assert mock_limit_service.call_count == 2
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
async def test_remove_product_success(mock_limit_service, db, test_team, test_product):
    """
    Test successful removal of a product from a team.

    GIVEN: A team with an active product
    WHEN: The product is removed from the team
    THEN: The product association is removed from the team's active products
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()
    mock_limit_instance.get_token_restrictions = Mock(return_value=(30, 50.0, 1000))

    # First apply the product to ensure it exists
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Remove the product
    await remove_product_from_team(db, test_team.stripe_customer_id, test_product.id)

    # Refresh team from database
    db.refresh(test_team)

    # Verify limit service was called with the correct team
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

    # Verify product was removed
    assert len(test_team.active_products) == 0

@pytest.mark.asyncio
async def test_remove_product_team_not_found(db, test_product):
    """
    Test removing a product when team is not found.

    GIVEN: A product exists but team does not
    WHEN: Attempting to remove the product
    THEN: The operation returns None
    """
    # Try to remove product from non-existent team
    result = await remove_product_from_team(db, "cus_nonexistent", test_product.id)
    assert result is None

@pytest.mark.asyncio
async def test_remove_product_product_not_found(db, test_team):
    """
    Test removing a non-existent product from a team.

    GIVEN: A team exists but product does not
    WHEN: Attempting to remove the product
    THEN: The operation returns None
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Try to remove non-existent product
    result = await remove_product_from_team(db, test_team.stripe_customer_id, "prod_nonexistent")
    assert result is None

@pytest.mark.asyncio
async def test_remove_product_not_active(db, test_team, test_product):
    """
    Test removing a product that is not active for a team.

    GIVEN: A team exists but does not have the specified product active
    WHEN: Attempting to remove the product
    THEN: The operation returns None
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Try to remove product that was never added
    result = await remove_product_from_team(db, test_team.stripe_customer_id, test_product.id)
    assert result is None

@pytest.mark.asyncio
async def test_remove_product_multiple_products(db, test_team, test_product):
    """
    Test removing one product while keeping others active.

    GIVEN: A team with multiple active products
    WHEN: One product is removed
    THEN: Only the specified product is removed, others remain active
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Create additional test product
    second_product = DBProduct(
        id="prod_test456",
        name="Test Product 2",
        user_count=5,
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
    db.add(second_product)
    db.commit()

    # Apply both products to the team
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))
    await apply_product_for_team(db, test_team.stripe_customer_id, second_product.id, datetime.now(UTC))

    # Remove only the first product
    await remove_product_from_team(db, test_team.stripe_customer_id, test_product.id)

    # Refresh team from database
    db.refresh(test_team)

    # Verify only the first product was removed
    assert len(test_team.active_products) == 1
    assert test_team.active_products[0].product.id == second_product.id

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_calls_limit_service(mock_litellm, mock_ses, mock_limit_service, db, test_team):
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
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_basic_metrics(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_product):
    """
    Test basic team monitoring metrics for teams with and without products.
    """
    # Setup test data
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)  # 15 days old
    db.add(test_team)
    db.commit()

    # Create a second team with a product and payment
    team_with_payment = DBTeam(
        name="Team With Payment",
        stripe_customer_id="cus_456",
        created_at=datetime.now(UTC) - timedelta(days=20),  # 20 days old
        last_payment=datetime.now(UTC) - timedelta(days=10)  # Last payment 10 days ago
    )
    db.add(team_with_payment)
    db.commit()

    # Add product to second team
    team_product = DBTeamProduct(
        team_id=team_with_payment.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(return_value={"info": {"spend": 0, "max_budget": 100, "key_alias": "test"}})

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify metrics for team without payment (age since creation)
    assert team_freshness_days.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() == 15

    # Verify metrics for team with payment (age since last payment)
    assert team_freshness_days.labels(
        team_id=str(team_with_payment.id),
        team_name=team_with_payment.name
    )._value.get() == 10

    # Verify limit service was called for both teams
    assert mock_limit_service.call_count == 2  # Called once for each team
    mock_limit_instance.set_team_limits.assert_called()

@pytest.mark.parametrize("team_age,expected_days_remaining,template_name", [
    (23, 7, "team-expiring"),
    (25, 5, "team-expiring"),
    (30, 0, "trial-expired"),
])
@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_notification_scenarios(mock_litellm, mock_ses, mock_limit_service, team_age, expected_days_remaining, template_name, db, test_team, test_team_admin):
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
    assert call_args['to_addresses'] == [test_team.admin_email]
    assert call_args['template_name'] == template_name
    assert call_args['template_data']['name'] == test_team.name

    # For trial-expired template, there's no days_remaining field
    if template_name == "team-expiring":
        assert call_args['template_data']['days_remaining'] == expected_days_remaining

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_key_expiration(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_region, test_team_key_creator):
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
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(return_value={
        "info": {
            "spend": 40.0,
            "max_budget": 50.0,
            "key_alias": "test-key"
        }
    })
    mock_litellm_instance.update_key_duration = AsyncMock()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify key was expired
    mock_litellm_instance.update_key_duration.assert_called_once_with("test_token", "0d")

    # Verify expired metric was incremented
    assert team_expired_metric.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() == 1

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_key_spend(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_region, test_team_key_creator):
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
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_litellm_instance = mock_litellm.return_value
    mock_litellm_instance.get_key_info = AsyncMock(return_value={
        "info": {
            "spend": 40.0,
            "max_budget": 50.0,
            "key_alias": "test-key"
        }
    })

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify key spend metrics
    assert key_spend_percentage.labels(
        team_id=str(test_team.id),
        team_name=test_team.name,
        key_alias="test-key"
    )._value.get() == 80.0  # 40/50 * 100

    # Verify team total spend
    assert team_total_spend.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() == 40.0

    # Verify key was not expired (team is not expired)
    mock_litellm_instance.update_key_duration.assert_not_called()

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
async def test_monitor_teams_active_labels(mock_litellm, mock_ses, mock_limit_service, db, test_team):
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
    assert team_freshness_days.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() == 0

    # Verify test team is no longer in active labels
    assert (str(test_team.id), test_team.name) not in active_team_labels

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called()

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
async def test_monitor_teams_error_handling(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_region):
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
        created_at=datetime.now(UTC)
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
    assert team_freshness_days.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() is not None

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_last_monitored_recently(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_team_admin):
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
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_last_monitored_old(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_team_admin):
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
    assert call_args['to_addresses'] == [test_team.admin_email]
    assert call_args['template_name'] == "team-expiring"
    assert call_args['template_data']['days_remaining'] == 7

    # Verify last_monitored was updated (since notifications were sent)
    db.refresh(test_team)
    assert test_team.last_monitored is not None
    assert test_team.last_monitored > old_last_monitored

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_last_monitored_none(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_team_admin):
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
    assert call_args['to_addresses'] == [test_team.admin_email]
    assert call_args['template_name'] == "team-expiring"
    assert call_args['template_data']['days_remaining'] == 7

    # Verify last_monitored was updated (since notifications were sent)
    db.refresh(test_team)
    assert test_team.last_monitored is not None

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_metrics_always_emitted(mock_litellm, mock_ses, mock_limit_service, db, test_team):
    """
    Test that metrics are always emitted regardless of last_monitored status.
    """
    # Setup test team with recent monitoring
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)
    expected_last_monitored = datetime.now(UTC) - timedelta(hours=12)  # Recently monitored
    test_team.last_monitored = expected_last_monitored
    db.add(test_team)
    db.commit()

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify metrics are still emitted even though team was recently monitored
    assert team_freshness_days.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() == 15

    # Verify last_monitored was not updated (no notifications sent)
    db.refresh(test_team)
    # Use approximate comparison due to timestamp precision differences
    assert abs((test_team.last_monitored - expected_last_monitored).total_seconds()) < 1

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_includes_renewal_period_check(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_product, test_region):
    """
    Test that the monitoring workflow includes renewal period checks when conditions are met.

    Given: A team with an active product that has passed its renewal period
    When: The monitoring workflow runs
    Then: The monitor_team_keys function should be called with renewal_period_days
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mocks
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(return_value={"info": {"spend": 0, "max_budget": 100, "key_alias": "test"}})

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
@patch('app.core.worker.LimitService')
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_does_not_include_renewal_period_check_when_not_passed(mock_litellm, mock_ses, mock_limit_service, db, test_team, test_product, test_region):
    """
    Test that the monitoring workflow does not include renewal period checks when conditions are not met.

    Given: A team with an active product but renewal period hasn't passed
    When: The monitoring workflow runs
    Then: The monitor_team_keys function should be called without renewal_period_days
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=15)  # 15 days ago (before 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mocks
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock(return_value={"info": {"spend": 0, "max_budget": 100, "key_alias": "test"}})

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
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_with_renewal_period_updates(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys updates keys after renewal period when LiteLLM has reset their budget within the last hour.

    Given: A team with keys that have had their budget reset within the last hour
    When: monitor_team_keys is called with renewal_period_days
    Then: The budget_duration should be updated to match the product renewal period
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create keys for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)

    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_456",
        region=test_region,
        owner_id=test_team_user.id
    )
    db.add(user_key)

    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    # Mock key info responses - both keys have different budget amounts, triggering updates
    mock_instance.get_key_info.side_effect = [
        # Team key - different budget amount triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "team_key",
                "spend": 0.0,
                "max_budget": 100.0,  # Different from expected (50.0)
                "budget_duration": "15d"
            }
        },
        # User key - different budget amount triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "user_key",
                "spend": 5.0,
                "max_budget": 25.0,  # Different from expected (50.0)
                "budget_duration": "15d"
            }
        }
    ]

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the combined function with renewal period days and budget amount
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify LiteLLM service was initialized correctly
    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # Verify get_key_info was called for both keys
    assert mock_instance.get_key_info.call_count == 2

    # Verify update_budget was called for both keys since they have different settings
    assert mock_instance.update_budget.call_count == 2

    # Check the first call (team key)
    first_call = mock_instance.update_budget.call_args_list[0]
    assert first_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert first_call[1]['budget_amount'] == test_product.max_budget_per_key
    # budget_duration should not be updated since it's not None and reset time doesn't align

    # Check the second call (user key)
    second_call = mock_instance.update_budget.call_args_list[1]
    assert second_call[0][0] == "user_token_456"  # First positional argument should be litellm_token
    assert second_call[1]['budget_amount'] == test_product.max_budget_per_key
    # budget_duration should not be updated since it's not None and reset time doesn't align

    # Verify team total spend is calculated correctly
    assert team_total == 5.0  # 0.0 + 5.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_with_renewal_period_updates_no_products(mock_litellm, db, test_team, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys updates budget_duration even when no products are found.

    Given: A team with keys that have had their budget reset within the last hour, but no active products
    When: monitor_team_keys is called with renewal_period_days
    Then: The budget_duration should be updated but budget_amount should not be set
    """
    # Setup test data - team with no products
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Create keys for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)

    user_key = DBPrivateAIKey(
        name="User Key",
        litellm_token="user_token_456",
        region=test_region,
        owner_id=test_team_user.id
    )
    db.add(user_key)

    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    # Mock key info responses - both keys have None budget_duration, triggering updates
    mock_instance.get_key_info.side_effect = [
        # Team key - None budget_duration triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "team_key",
                "spend": 0.0,
                "max_budget": 100.0,
                "budget_duration": None  # None triggers update
            }
        },
        # User key - None budget_duration triggers update
        {
            "info": {
                "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
                "key_alias": "user_key",
                "spend": 5.0,
                "max_budget": 50.0,
                "budget_duration": None  # None triggers update
            }
        }
    ]

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the combined function with renewal period days (no budget amount)
    team_total = await monitor_team_keys(test_team, keys_by_region, False, 30, None)  # Use default 30 days, no budget amount

    # Verify LiteLLM service was initialized correctly
    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # Verify get_key_info was called for both keys
    assert mock_instance.get_key_info.call_count == 2

    # Verify update_budget was called for both keys since they have None budget_duration
    assert mock_instance.update_budget.call_count == 2

    # Check the first call (team key)
    first_call = mock_instance.update_budget.call_args_list[0]
    assert first_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert first_call[0][1] == "30d"  # Second positional argument should be budget_duration
    # Should not have budget_amount since no products were found
    assert first_call[1]['budget_amount'] is None

    # Check the second call (user key)
    second_call = mock_instance.update_budget.call_args_list[1]
    assert second_call[0][0] == "user_token_456"  # First positional argument should be litellm_token
    assert second_call[0][1] == "30d"  # Second positional argument should be budget_duration
    # Should not have budget_amount since no products were found
    assert second_call[1]['budget_amount'] is None

    # Verify team total spend is calculated correctly
    assert team_total == 5.0  # 0.0 + 5.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_reset_time_alignment_heuristic(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys updates when reset time alignment heuristic is met.

    Given: A team with keys where (now + current_budget_duration) is within 1 hour of budget_reset_at
    When: monitor_team_keys is called with renewal_period_days
    Then: The budget should be updated due to reset time alignment
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - current budget_duration is 15d, budget_reset_at is 30 minutes after (now + 15d)
    # This should trigger the reset time alignment heuristic
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time + timedelta(days=15, minutes=30)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": 100.0,
            "budget_duration": "15d"  # Different from expected
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called because of reset time alignment
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
    assert update_call[1]['budget_amount'] == test_product.max_budget_per_key

    # Verify team total spend is calculated correctly
    assert team_total == 10.0





@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_none_budget_duration_handled(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys handles None budget_duration gracefully.

    Given: A team with keys where budget_duration is None
    When: monitor_team_keys is called with renewal_period_days
    Then: The function should not error and should skip the reset time alignment heuristic
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - budget_duration is None, but spend is non-zero
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time + timedelta(days=30)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,  # Non-zero spend
            "max_budget": 100.0,
            "budget_duration": None  # None budget_duration
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called because budget_duration is None (forces update)
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
    assert update_call[1]['budget_amount'] == test_product.max_budget_per_key

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_duration_mismatch_without_reset_time_alignment(mock_litellm, db, test_team, test_product, test_region):
    """
    Test that duration mismatches do not trigger updates when reset time is not within an hour of (now + current duration).

    Given: A team with keys where duration mismatches but reset time is not within 1 hour of (now + current duration)
    When: monitor_team_keys is called with renewal_period_days
    Then: The budget_duration should not be updated
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - duration mismatches but reset time is not within 1 hour of (now + current duration)
    # Current duration is 15d, expected is 30d
    # Reset time is 2 hours after (now + 15d), which is not within 1 hour of (now + 15d)
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time + timedelta(days=15, hours=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": 50.0,  # Same as expected
            "budget_duration": "15d"  # Different from expected (30d)
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was not called because reset time doesn't align
    assert mock_instance.update_budget.call_count == 0

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_both_mismatch_with_reset_time_alignment(mock_litellm, db, test_team, test_product, test_region):
    """
    Test that both budget amount and duration mismatches trigger updates when reset time aligns.

    Given: A team with keys where both budget amount and duration mismatch, and reset time aligns
    When: monitor_team_keys is called with renewal_period_days and max_budget_amount
    Then: Both budget_amount and budget_duration should be updated
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - both budget amount and duration mismatch, and reset time aligns
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time + timedelta(days=15, minutes=30)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": 25.0,  # Different from expected (50.0)
            "budget_duration": "15d"  # Different from expected (30d)
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called with both budget_amount and budget_duration
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
    assert update_call[1]['budget_amount'] == test_product.max_budget_per_key

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_both_mismatch_without_reset_time_alignment(mock_litellm, db, test_team, test_product, test_region):
    """
    Test that only budget amount is updated when both mismatch but reset time doesn't align.

    Given: A team with keys where both budget amount and duration mismatch, but reset time doesn't align
    When: monitor_team_keys is called with renewal_period_days and max_budget_amount
    Then: Only budget_amount should be updated, not budget_duration
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - both budget amount and duration mismatch, but reset time doesn't align
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time + timedelta(days=15, hours=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": 25.0,  # Different from expected (50.0)
            "budget_duration": "15d"  # Different from expected (30d)
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called with only budget_amount
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] is None  # Second positional argument should be None (no budget_duration update)
    assert update_call[1]['budget_amount'] == test_product.max_budget_per_key

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_duration_parsing_different_units(mock_litellm, db, test_team, test_product, test_region):
    """
    Test that duration parsing handles different time units correctly.

    Given: A team with keys where duration uses different time units (minutes, hours, days)
    When: monitor_team_keys is called with renewal_period_days
    Then: The duration parsing should work correctly for all supported units
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Test cases for different duration units
    test_cases = [
        # (current_duration, reset_time_offset, should_update)
        ("30m", timedelta(minutes=30, seconds=30), True),   # 30 minutes, reset 30 seconds after period end
        ("2h", timedelta(hours=2, minutes=30), True),       # 2 hours, reset 30 minutes after period end
        ("15d", timedelta(days=15, hours=2), False),        # 15 days, reset 2 hours after period end (too far)
        ("1h", timedelta(hours=1, minutes=30), True),       # 1 hour, reset 30 minutes after period end
    ]

    for current_duration, reset_time_offset, should_update in test_cases:
        # Use a fresh current_time for each iteration to ensure consistent calculations
        iteration_time = datetime.now(UTC)

        # Mock key info response with different duration units
        mock_instance.get_key_info.return_value = {
            "info": {
                "budget_reset_at": (iteration_time + reset_time_offset).isoformat(),
                "key_alias": "team_key",
                "spend": 10.0,
                "max_budget": 50.0,  # Same as expected
                "budget_duration": current_duration  # Different from expected (30d)
            }
        }

        # Get keys by region
        keys_by_region = get_team_keys_by_region(db, test_team.id)

        # Reset mock call count
        mock_instance.update_budget.reset_mock()

        # Call the function with renewal period days
        team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

        # Verify update_budget was called or not based on the test case
        if should_update:
            assert mock_instance.update_budget.call_count == 1
            update_call = mock_instance.update_budget.call_args
            assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
            assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
        else:
            assert mock_instance.update_budget.call_count == 0

        # Verify team total spend is calculated correctly
        assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_zero_duration_renewal(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys properly renews keys with "0d" duration.

    Given: A team with an active product and a key that has been incorrectly set to "0d" duration
    When: monitor_team_keys is called with renewal_period_days
    Then: The key should be updated to the correct duration regardless of reset time alignment
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)

    # Mock key info response - key has "0d" duration (expired due to bug)
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),  # Reset time in the past
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": 100.0,
            "budget_duration": "0d"  # Expired key due to bug
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called to fix the "0d" duration
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
    assert update_call[1]['budget_amount'] == test_product.max_budget_per_key

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_update_budget_parameter_issue(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that update_budget is called with correct parameters when budget amount needs updating.

    GIVEN: A team with a product and keys that have different budget amounts
    WHEN: monitor_team_keys is called with renewal period and budget amount
    THEN: update_budget should be called with litellm_token as first positional argument, not as keyword argument
    """
    # Set up team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    # Mock key info response - different budget amount triggers update
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (datetime.now(UTC) + timedelta(days=30)).isoformat(),
            "key_alias": "test_key",
            "spend": 0.0,
            "max_budget": 27.0,  # Different from expected (120.0)
            "budget_duration": "30d"
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days and budget amount
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called with correct parameters
    assert mock_instance.update_budget.call_count == 1

    # Check that litellm_token is passed as first positional argument, not as keyword
    call_args = mock_instance.update_budget.call_args
    # After the fix, litellm_token should be the first positional argument
    assert call_args[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert call_args[1]['budget_amount'] == test_product.max_budget_per_key  # budget_amount as keyword argument

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_expiry_within_next_month(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys updates keys that expire within the next month.

    Given: A team with an active product and a key that expires within the next 30 days
    When: monitor_team_keys is called with renewal_period_days
    Then: The key should be updated to the renewal period duration
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)
    # Set expiry date to 15 days from now (within the 30-day window)
    expiry_date = current_time + timedelta(days=15)

    # Mock key info response - key expires within next month
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": test_product.max_budget_per_key,  # Use the same budget amount to avoid Rule 1 trigger
            "budget_duration": "30d",
            "expires": expiry_date.isoformat()
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called to update the duration for expiring key
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
    # When updating for expiry reasons, budget_amount should be None since we're only updating duration
    assert update_call[1]['budget_amount'] is None

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_expired_key(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys updates keys that are already expired.

    Given: A team with an active product and a key that has already expired
    When: monitor_team_keys is called with renewal_period_days
    Then: The key should be updated to the renewal period duration
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)
    # Set expiry date to 5 days ago (already expired)
    expiry_date = current_time - timedelta(days=5)

    # Mock key info response - key is already expired
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": test_product.max_budget_per_key,  # Use the same budget amount to avoid Rule 1 trigger
            "budget_duration": "30d",
            "expires": expiry_date.isoformat()
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was called to update the duration for expired key
    assert mock_instance.update_budget.call_count == 1
    update_call = mock_instance.update_budget.call_args
    assert update_call[0][0] == "team_token_123"  # First positional argument should be litellm_token
    assert update_call[0][1] == f"{test_product.renewal_period_days}d"  # Second positional argument should be budget_duration
    # When updating for expiry reasons, budget_amount should be None since we're only updating duration
    assert update_call[1]['budget_amount'] is None

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LiteLLMService')
async def test_monitor_team_keys_expiry_beyond_next_month(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
    """
    Test that monitor_team_keys does not update keys that expire beyond the next month.

    Given: A team with an active product and a key that expires beyond the next 30 days
    When: monitor_team_keys is called with renewal_period_days
    Then: The key should not be updated for expiry reasons
    """
    # Setup test data
    test_team.last_payment = datetime.now(UTC) - timedelta(days=35)  # 35 days ago (past 30-day renewal period)
    db.add(test_team)

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)

    # Create a key for the team
    team_key = DBPrivateAIKey(
        name="Team Key",
        litellm_token="team_token_123",
        region=test_region,
        team_id=test_team.id
    )
    db.add(team_key)
    db.commit()

    # Setup mock LiteLLM service
    mock_instance = mock_litellm.return_value
    mock_instance.get_key_info = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    current_time = datetime.now(UTC)
    # Set expiry date to 45 days from now (beyond the 30-day window)
    expiry_date = current_time + timedelta(days=45)

    # Mock key info response - key expires beyond next month
    mock_instance.get_key_info.return_value = {
        "info": {
            "budget_reset_at": (current_time - timedelta(days=2)).isoformat(),
            "key_alias": "team_key",
            "spend": 10.0,
            "max_budget": test_product.max_budget_per_key,  # Use the same budget amount to avoid Rule 1 trigger
            "budget_duration": "30d",
            "expires": expiry_date.isoformat()
        }
    }

    # Get keys by region
    keys_by_region = get_team_keys_by_region(db, test_team.id)

    # Call the function with renewal period days
    team_total = await monitor_team_keys(test_team, keys_by_region, False, test_product.renewal_period_days, test_product.max_budget_per_key)

    # Verify update_budget was not called for expiry reasons
    assert mock_instance.update_budget.call_count == 0

    # Verify team total spend is calculated correctly
    assert team_total == 10.0

@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.LiteLLMService')
async def test_monitor_teams_populates_team_metrics(mock_litellm_class, mock_limit_service, db, test_team, test_region):
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
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Mock LiteLLM service responses
    mock_litellm_service = AsyncMock()
    mock_litellm_class.return_value = mock_litellm_service
    mock_litellm_service.get_key_info.return_value = {
        "info": {
            "spend": 75.50,
            "max_budget": 100.0,
            "key_alias": "test-key"
        }
    }

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Act
    await monitor_teams(db)

    # Assert
    metrics = db.query(DBTeamMetrics).filter(DBTeamMetrics.team_id == test_team.id).first()
    assert metrics is not None
    assert metrics.total_spend == 75.50
    assert test_region.name in metrics.regions
    assert metrics.last_spend_calculation is not None

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)


@pytest.mark.asyncio
@patch('app.core.worker.LimitService')
@patch('app.core.worker.LiteLLMService')
async def test_monitor_teams_updates_existing_metrics(mock_litellm_class, mock_limit_service, db, test_team, test_region):
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
        last_updated=old_timestamp
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
        created_at=datetime.now(UTC)
    )
    db.add(test_key)
    db.commit()

    # Mock LiteLLM service responses
    mock_litellm_service = AsyncMock()
    mock_litellm_class.return_value = mock_litellm_service
    mock_litellm_service.get_key_info.return_value = {
        "info": {
            "spend": 125.75,
            "max_budget": 200.0,
            "key_alias": "test-key"
        }
    }

    # Setup mock limit service
    mock_limit_instance = mock_limit_service.return_value
    mock_limit_instance.set_team_limits = Mock()

    # Act
    await monitor_teams(db)

    # Assert
    updated_metrics = db.query(DBTeamMetrics).filter(DBTeamMetrics.team_id == test_team.id).first()
    assert updated_metrics is not None
    assert updated_metrics.total_spend == 125.75
    assert test_region.name in updated_metrics.regions
    assert updated_metrics.last_updated > old_update_date

    # Verify limit service was called
    mock_limit_service.assert_called_with(db)
    mock_limit_instance.set_team_limits.assert_called_with(test_team)

