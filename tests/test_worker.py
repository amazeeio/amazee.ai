import pytest
from app.db.models import DBProduct, DBTeamProduct, DBPrivateAIKey, DBTeam, DBUser
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
    active_team_labels
)
from unittest.mock import AsyncMock, patch, Mock

@pytest.mark.asyncio
@patch('app.core.worker.get_product_id_from_subscription', new_callable=AsyncMock)
async def test_handle_subscription_deleted(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "customer.subscription.deleted"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_subscription.id = "sub_123"
    mock_event.data.object = mock_subscription

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("sub_123")
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

@patch('app.core.worker.get_product_id_from_session', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_checkout_session_async_payment_failed(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "checkout.session.async_payment_failed"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@patch('app.core.worker.get_product_id_from_session', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_checkout_session_expired(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "checkout.session.expired"
    mock_session = Mock()
    mock_session.metadata = {"team_id": str(test_team.id)}
    mock_session.customer = "cus_123"
    mock_session.id = "cs_123"
    mock_event.data.object = mock_session

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("cs_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None

@patch('app.core.worker.get_product_id_from_subscription', new_callable=AsyncMock)
@pytest.mark.asyncio
async def test_handle_subscription_paused(mock_get_product, db, test_team, test_product):
    # Arrange
    test_team.stripe_customer_id = "cus_123"
    db.add(test_team)
    db.commit()

    mock_event = Mock()
    mock_event.type = "customer.subscription.paused"
    mock_subscription = Mock()
    mock_subscription.customer = "cus_123"
    mock_subscription.id = "sub_123"
    mock_event.data.object = mock_subscription

    mock_get_product.return_value = test_product.id

    # Set up initial team-product association
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Act
    await handle_stripe_event_background(mock_event, db)

    # Assert
    mock_get_product.assert_called_once_with("sub_123")
    # Verify team-product association was removed
    team_product = db.query(DBTeamProduct).filter(
        DBTeamProduct.team_id == test_team.id,
        DBTeamProduct.product_id == test_product.id
    ).first()
    assert team_product is None


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
@patch('app.core.worker.LiteLLMService')
async def test_apply_product_extends_keys_and_sets_budget(mock_litellm, db, test_team, test_product, test_region, test_team_user, test_team_key_creator):
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

    # Verify team was updated correctly
    db.refresh(test_team)
    assert len(test_team.active_products) == 1
    assert test_team.active_products[0].product.id == test_product.id
    assert test_team.last_payment is not None

@pytest.mark.asyncio
async def test_remove_product_success(db, test_team, test_product):
    """
    Test successful removal of a product from a team.

    GIVEN: A team with an active product
    WHEN: The product is removed from the team
    THEN: The product association is removed from the team's active products
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # First apply the product to ensure it exists
    await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id, datetime.now(UTC))

    # Remove the product
    await remove_product_from_team(db, test_team.stripe_customer_id, test_product.id)

    # Refresh team from database
    db.refresh(test_team)

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
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_basic_metrics(mock_litellm, mock_ses, db, test_team, test_product):
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

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_expiration_notification(mock_litellm, mock_ses, db, test_team, test_team_admin):
    """
    Test first expiration notification for teams approaching expiration (7 days remaining).
    """
    # Setup test team approaching expiration (23 days old, 7 days remaining)
    test_team.created_at = datetime.now(UTC) - timedelta(days=23)
    test_team.admin_email = test_team_admin.email  # Use the admin fixture's email
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify email was sent
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args['to_addresses'] == [test_team.admin_email]
    assert call_args['template_name'] == "team-expiring"
    assert call_args['template_data']['name'] == test_team.name
    assert call_args['template_data']['days_remaining'] == 7  # 30 - 23

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_expiration_notification_second(mock_litellm, mock_ses, db, test_team, test_team_admin):
    """
    Test second expiration notification for teams approaching expiration (5 days remaining).
    """
    # Setup test team approaching expiration (25 days old, 5 days remaining)
    test_team.created_at = datetime.now(UTC) - timedelta(days=25)
    test_team.admin_email = test_team_admin.email  # Use the admin fixture's email
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify email was sent
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args['to_addresses'] == [test_team.admin_email]
    assert call_args['template_name'] == "team-expiring"
    assert call_args['template_data']['name'] == test_team.name
    assert call_args['template_data']['days_remaining'] == 5  # 30 - 25

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_trial_expired_notification(mock_litellm, mock_ses, db, test_team, test_team_admin):
    """
    Test trial expired notification for teams that have reached expiration.
    """
    # Setup test team at expiration (30 days old)
    test_team.created_at = datetime.now(UTC) - timedelta(days=30)
    test_team.admin_email = test_team_admin.email
    db.add(test_team)
    db.commit()

    # Setup mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email = Mock()

    # Run monitoring
    await monitor_teams(db)

    # Verify expired email was sent
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args[1]
    assert call_args['to_addresses'] == [test_team.admin_email]
    assert call_args['template_name'] == "trial-expired"
    assert call_args['template_data']['name'] == test_team.name

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_key_expiration(mock_litellm, mock_ses, db, test_team, test_region, test_team_key_creator):
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

    # Run monitoring
    await monitor_teams(db)

    # Verify key was expired
    mock_litellm_instance.update_key_duration.assert_called_once_with("test_token", "0d")

    # Verify expired metric was incremented
    assert team_expired_metric.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() == 1

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_key_spend(mock_litellm, mock_ses, db, test_team, test_region, test_team_key_creator):
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

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
async def test_monitor_teams_active_labels(mock_litellm, mock_ses, db, test_team):
    """
    Test handling of active team labels.
    """
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

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
async def test_monitor_teams_error_handling(mock_litellm, mock_ses, db, test_team, test_region):
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

    # Run monitoring - should not raise exception
    await monitor_teams(db)

    # Verify team metrics are still set
    assert team_freshness_days.labels(
        team_id=str(test_team.id),
        team_name=test_team.name
    )._value.get() is not None

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_last_monitored_recently(mock_litellm, mock_ses, db, test_team, test_team_admin):
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

    # Run monitoring
    await monitor_teams(db)

    # Verify no email was sent (team was recently monitored)
    mock_ses_instance.send_email.assert_not_called()

    # Verify last_monitored was not updated (since no notifications were sent)
    db.refresh(test_team)
    # Use approximate comparison due to timestamp precision differences
    assert abs((test_team.last_monitored - expected_last_monitored).total_seconds()) < 1

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_last_monitored_old(mock_litellm, mock_ses, db, test_team, test_team_admin):
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

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_last_monitored_none(mock_litellm, mock_ses, db, test_team, test_team_admin):
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

@pytest.mark.asyncio
@patch('app.core.worker.SESService')
@patch('app.core.worker.LiteLLMService')
@patch('app.core.config.settings.ENABLE_LIMITS', True)
async def test_monitor_teams_metrics_always_emitted(mock_litellm, mock_ses, db, test_team):
    """
    Test that metrics are always emitted regardless of last_monitored status.
    """
    # Setup test team with recent monitoring
    test_team.created_at = datetime.now(UTC) - timedelta(days=15)
    expected_last_monitored = datetime.now(UTC) - timedelta(hours=12)  # Recently monitored
    test_team.last_monitored = expected_last_monitored
    db.add(test_team)
    db.commit()

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