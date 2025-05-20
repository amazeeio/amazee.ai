import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session
from app.db.models import DBProduct, DBTeam, DBUser, DBPrivateAIKey
from datetime import datetime, UTC, timedelta
from app.core.worker import apply_product_for_team
from unittest.mock import AsyncMock, patch

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
    result = await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id)

    # Verify the result
    assert result is True

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
    THEN: The operation returns False
    """
    # Try to apply product to non-existent team
    result = await apply_product_for_team(db, "cus_nonexistent", test_product.id)
    assert result is False

@pytest.mark.asyncio
async def test_apply_product_product_not_found(db, test_team):
    """
    Test applying a non-existent product to a team.

    GIVEN: A team exists but product does not
    WHEN: Attempting to apply the product
    THEN: The operation returns False
    """
    # Set stripe customer ID for the test team
    test_team.stripe_customer_id = "cus_test123"
    db.commit()

    # Try to apply non-existent product
    result = await apply_product_for_team(db, test_team.stripe_customer_id, "prod_nonexistent")
    assert result is False

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
        result = await apply_product_for_team(db, test_team.stripe_customer_id, product.id)
        assert result is True

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
    result = await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id)
    assert result is True

    # Get the initial last payment date
    db.refresh(test_team)
    initial_last_payment = test_team.last_payment

    # Apply the same product again
    result = await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id)
    assert result is True

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

    GIVEN: A team with users and keys (both team-owned and user-owned), and a product which specifies a max_budget of $20 per key
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
    mock_instance.update_key_duration = AsyncMock()
    mock_instance.update_budget = AsyncMock()

    # Apply product to team
    result = await apply_product_for_team(db, test_team.stripe_customer_id, test_product.id)
    assert result is True

    # Verify LiteLLM service was initialized with correct region settings
    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url,
        api_key=test_region.litellm_api_key
    )

    # Verify LiteLLM service was called for all keys (both team and user owned)
    all_keys = team_keys + user_keys
    assert mock_instance.update_key_duration.call_count == len(all_keys)
    assert mock_instance.update_budget.call_count == len(all_keys)

    # Verify each key was updated with correct duration and budget
    for key in all_keys:
        # Verify duration update
        duration_calls = [call for call in mock_instance.update_key_duration.call_args_list
                        if call[1]['litellm_token'] == key.litellm_token]
        assert len(duration_calls) == 1
        assert duration_calls[0][1]['duration'] == f"{test_product.renewal_period_days}d"

        # Verify budget update
        budget_calls = [call for call in mock_instance.update_budget.call_args_list
                      if call[1]['litellm_token'] == key.litellm_token]
        assert len(budget_calls) == 1
        assert budget_calls[0][1]['budget_duration'] == f"{test_product.renewal_period_days}d"
        assert budget_calls[0][1]['budget_amount'] == test_product.max_budget_per_key

    # Verify team was updated correctly
    db.refresh(test_team)
    assert len(test_team.active_products) == 1
    assert test_team.active_products[0].product.id == test_product.id
    assert test_team.last_payment is not None