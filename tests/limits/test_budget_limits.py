import logging
import threading
import time
import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import patch, AsyncMock
from app.db.models import DBUser, DBProduct, DBTeamProduct, DBPrivateAIKey
from app.core.limit_service import LimitService
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource
from app.core.security import get_password_hash
from app.core.limit_service import (
    DEFAULT_KEY_DURATION,
    DEFAULT_MAX_SPEND,
    DEFAULT_RPM_PER_KEY,
)


def test_get_token_restrictions_default_limits(db, test_team):
    """
    GIVEN: A team with no products
    WHEN: Getting token restrictions
    THEN: Default limits are returned
    """
    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should use default values since team has no products
    assert days_left == DEFAULT_KEY_DURATION  # 30 days
    assert max_spend == DEFAULT_MAX_SPEND  # 27.0
    assert rpm_limit == DEFAULT_RPM_PER_KEY  # 500


def test_get_token_restrictions_with_product(db, test_team, test_product):
    """
    GIVEN: A team with a product
    WHEN: Getting token restrictions
    THEN: Product limits are returned
    """
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should use product values
    assert days_left == test_product.renewal_period_days  # 30 days
    assert max_spend == test_product.max_budget_per_key  # 50.0
    assert rpm_limit == test_product.rpm_per_key  # 1000


def test_get_token_restrictions_with_multiple_products(db, test_team):
    """
    GIVEN: A team with multiple products with different limits
    WHEN: Getting token restrictions
    THEN: Maximum values from all products are returned
    """
    # Create two products with different limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=3,
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
    product2 = DBProduct(
        id="prod_test2",
        name="Test Product 2",
        user_count=3,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=75.0,  # Higher budget
        rpm_per_key=2000,  # Higher RPM
        vector_db_count=1,
        vector_db_storage=100,
        renewal_period_days=60,  # Longer duration
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product1)
    db.add(product2)
    db.commit()

    # Add both products to team
    team_product1 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product1.id
    )
    team_product2 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product2.id
    )
    db.add(team_product1)
    db.add(team_product2)
    db.commit()

    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should use the maximum values from both products
    assert days_left == product2.renewal_period_days  # 60 days
    assert max_spend == product2.max_budget_per_key  # 75.0
    assert rpm_limit == product2.rpm_per_key  # 2000


def test_get_token_restrictions_with_payment_history(db, test_team, test_product):
    """
    GIVEN: A team with payment history
    WHEN: Getting token restrictions
    THEN: Product renewal period is returned, not calculated days left
    """
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Set created_at to 30 days ago and last_payment to 15 days ago
    now = datetime.now(UTC)
    test_team.created_at = now - timedelta(days=30)
    test_team.last_payment = now - timedelta(days=15)
    db.commit()

    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)

    # Should return the product's renewal_period_days, not calculated days left
    assert days_left == test_product.renewal_period_days  # 30 days
    assert max_spend == test_product.max_budget_per_key
    assert rpm_limit == test_product.rpm_per_key


def test_get_token_restrictions_team_not_found(db):
    """
    GIVEN: A non-existent team ID
    WHEN: Getting token restrictions
    THEN: HTTPException with 404 status is raised
    """
    from fastapi import HTTPException

    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.get_token_restrictions(99999)  # Non-existent team ID
    assert exc_info.value.status_code == 404
    assert "Team not found" in str(exc_info.value.detail)


def test_get_token_restrictions_with_limit_service(db, test_team):
    """
    GIVEN: A team with budget and RPM limits set up in the new limit service
    WHEN: Getting token restrictions
    THEN: The limit service is used first and returns the correct values
    """

    # Set up budget and RPM limits in the new service
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=100.0,
        limited_by=LimitSource.DEFAULT
    )
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.RPM,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.COUNT,
        max_value=1500.0,
        limited_by=LimitSource.DEFAULT
    )

    # Test that get_token_restrictions returns the limit service values
    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)
    assert days_left == DEFAULT_KEY_DURATION  # Still uses product/default for duration
    assert max_spend == 100.0  # From limit service
    assert rpm_limit == 1500.0  # From limit service


def test_get_token_restrictions_with_limit_service_and_products(db, test_team, test_product):
    """
    GIVEN: A team with both limit service limits and product limits
    WHEN: Getting token restrictions
    THEN: The limit service values take precedence over product values
    """

    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Set up budget and RPM limits in the new service (different from product)
    limit_service = LimitService(db)
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        max_value=200.0,  # Different from product's 50.0
        limited_by=LimitSource.DEFAULT
    )
    limit_service.set_limit(
        owner_type=OwnerType.TEAM,
        owner_id=test_team.id,
        resource_type=ResourceType.RPM,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.COUNT,
        max_value=2500.0,  # Different from product's 1000
        limited_by=LimitSource.DEFAULT
    )

    # Test that get_token_restrictions returns the limit service values
    limit_service = LimitService(db)
    days_left, max_spend, rpm_limit = limit_service.get_token_restrictions(test_team.id)
    assert days_left == test_product.renewal_period_days  # Still uses product for duration
    assert max_spend == 200.0  # From limit service, not product
    assert rpm_limit == 2500.0  # From limit service, not product


def test_get_product_max_by_type_no_products(db, test_team):
    """
    GIVEN: A team with no associated products
    WHEN: Getting the product limit for a resource
    THEN: None is returned
    """
    limit_service = LimitService(db)
    max_vectors = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.VECTOR_DB)
    assert max_vectors is None


def test_get_product_max_by_type_multiple_products(db, test_team):
    """
    GIVEN: A team with two associated products
    WHEN: Getting the product limit for a resource
    THEN: The maximum value across all products is returned
    """
    # Create two products with different vector DB limits
    product1 = DBProduct(
        id="prod_test1",
        name="Test Product 1",
        user_count=4,
        keys_per_user=2,
        total_key_count=10,
        service_key_count=2,
        max_budget_per_key=150.0,
        rpm_per_key=1000,
        vector_db_count=2,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    product2 = DBProduct(
        id="prod_test2",
        name="Test Product 2",
        user_count=3,
        keys_per_user=2,
        total_key_count=15,
        service_key_count=2,
        max_budget_per_key=50.0,
        rpm_per_key=800,
        vector_db_count=3,
        vector_db_storage=100,
        renewal_period_days=30,
        active=True,
        created_at=datetime.now(UTC)
    )
    db.add(product1)
    db.add(product2)
    db.commit()

    # Add both products to team
    team_product1 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product1.id
    )
    team_product2 = DBTeamProduct(
        team_id=test_team.id,
        product_id=product2.id
    )
    db.add(team_product1)
    db.add(team_product2)
    db.commit()

    limit_service = LimitService(db)
    max_vectors = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.VECTOR_DB)
    max_users = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.USER)
    max_keys = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.SERVICE_KEY)
    max_budget = limit_service.get_team_product_limit_for_resource(test_team.id, ResourceType.BUDGET)
    assert max_vectors == 3
    assert max_users == 4
    assert max_keys == 2  # Now returns max service_key_count, not total_key_count
    assert max_budget == 150.0


@patch('app.core.team_service.LiteLLMService')
def test_budget_propagation_session_isolation(mock_litellm_class, db, test_team, test_region):
    """
    GIVEN: A team with keys and a LimitService instance
    WHEN: _trigger_team_budget_propagation is called and the original session is closed
    THEN: The propagation should still work because it creates its own session

    This test verifies the fix for the SQLAlchemy session isolation issue where
    background threads were incorrectly reusing the request's session.
    """
    # Create a user in the team
    team_user = DBUser(
        email="sessiontest@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        team_id=test_team.id
    )
    db.add(team_user)
    db.commit()
    db.refresh(team_user)

    # Create a key for the team
    key1 = DBPrivateAIKey(
        name="Session Test Key",
        litellm_token="session_test_token",
        litellm_api_url=test_region.litellm_api_url,
        team_id=test_team.id,
        owner_id=team_user.id,
        region_id=test_region.id
    )
    db.add(key1)
    db.commit()

    # Track calls
    captured_calls = []
    call_lock = threading.Lock()

    async def mock_update_budget(*args, **kwargs):
        with call_lock:
            captured_calls.append((args, kwargs))

    def create_mock_instance(*args, **kwargs):
        mock_instance = AsyncMock()
        mock_instance.update_budget = mock_update_budget
        return mock_instance
    mock_litellm_class.side_effect = create_mock_instance

    # Create a LimitService and call the method that triggers background propagation
    limit_service = LimitService(db)

    # Call the actual method that has the bug - it spawns a background thread
    limit_service._trigger_team_budget_propagation(test_team.id, 100.0)

    # Simulate what happens in production: close the original session immediately
    # This is what FastAPI does after the request completes
    db.close()

    # Wait for background thread to complete
    max_wait = 50  # 5 seconds
    waited = 0
    while waited < max_wait:
        time.sleep(0.1)
        waited += 1
        with call_lock:
            if len(captured_calls) >= 1:
                break

    # Verify the call was made successfully
    with call_lock:
        assert len(captured_calls) == 1, f"Expected 1 call but got {len(captured_calls)}"

        # Verify correct parameters
        args, kwargs = captured_calls[0]
        assert kwargs.get("budget_amount") == 100.0


@patch('app.core.team_service.LiteLLMService')
def test_budget_propagation_works_after_session_close(mock_litellm_class, db, test_team, test_region):
    """
    GIVEN: A team with keys and budget limits
    WHEN: The limit is updated via set_limit and the original session is immediately closed
    THEN: The background propagation should still work (uses its own session)

    This test verifies the fix for SQLAlchemy session isolation errors:
    - "This session is in 'prepared' state"
    - "Instance is not bound to a Session"
    """
    # Create a user in the team
    team_user = DBUser(
        email="sessiontest@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        team_id=test_team.id
    )
    db.add(team_user)
    db.commit()
    db.refresh(team_user)

    # Create a key for the team
    key1 = DBPrivateAIKey(
        name="Session Test Key",
        litellm_token="session_test_token",
        litellm_api_url=test_region.litellm_api_url,
        team_id=test_team.id,
        owner_id=team_user.id,
        region_id=test_region.id
    )
    db.add(key1)
    db.commit()

    # Track calls and errors
    captured_calls = []
    propagation_errors = []
    call_lock = threading.Lock()

    async def mock_update_budget(*args, **kwargs):
        with call_lock:
            captured_calls.append((args, kwargs))

    def create_mock_instance(*args, **kwargs):
        mock_instance = AsyncMock()
        mock_instance.update_budget = mock_update_budget
        return mock_instance
    mock_litellm_class.side_effect = create_mock_instance

    # Patch logger.error to capture propagation errors
    original_error = logging.getLogger('app.core.team_service').error
    def capture_error(msg, *args, **kwargs):
        with call_lock:
            propagation_errors.append(msg)
        original_error(msg, *args, **kwargs)

    with patch.object(logging.getLogger('app.core.team_service'), 'error', capture_error):
        # Create limit service and set a budget limit - this triggers _trigger_team_budget_propagation
        limit_service = LimitService(db)
        limit_service.set_limit(
            owner_type=OwnerType.TEAM,
            owner_id=test_team.id,
            resource_type=ResourceType.BUDGET,
            limit_type=LimitType.DATA_PLANE,
            unit=UnitType.DOLLAR,
            max_value=150.0,
            limited_by=LimitSource.DEFAULT
        )

        # Immediately close the session - this simulates what happens when the request ends
        # The background thread should still work because it creates its own session
        db.close()

        # Wait for background thread to complete
        max_wait = 50  # 5 seconds
        waited = 0
        while waited < max_wait:
            time.sleep(0.1)
            waited += 1
            with call_lock:
                if len(captured_calls) >= 1 or len(propagation_errors) >= 1:
                    break

    # Verify no errors occurred during propagation
    with call_lock:
        error_msgs = [e for e in propagation_errors if 'Error propagating budget' in e or 'is not bound to a Session' in e]
        assert len(error_msgs) == 0, f"Propagation errors occurred: {error_msgs}"

        # Verify the propagation succeeded
        assert len(captured_calls) == 1, f"Expected 1 call but got {len(captured_calls)}. Background propagation failed."

        # Verify correct parameters
        args, kwargs = captured_calls[0]
        assert kwargs.get("budget_amount") == 150.0


@patch('app.core.team_service.LiteLLMService')
def test_overwrite_team_budget_limit_propagates_to_keys(mock_litellm_class, client, admin_token, test_team, test_region, db):
    """
    GIVEN: A team with multiple private AI keys
    WHEN: The team's budget limit is updated via the overwrite_limit API
    THEN: All keys belonging to the team should have their budgets updated in LiteLLM
    """
    # Create a user in the team
    team_user = DBUser(
        email="teamuser@example.com",
        hashed_password=get_password_hash("password123"),
        is_active=True,
        team_id=test_team.id
    )
    db.add(team_user)
    db.commit()
    db.refresh(team_user)

    # Create multiple keys for the team (both user-owned and team-owned)
    key1 = DBPrivateAIKey(
        name="Key 1",
        litellm_token="token1",
        litellm_api_url=test_region.litellm_api_url,
        team_id=test_team.id,
        owner_id=team_user.id,
        region_id=test_region.id
    )
    key2 = DBPrivateAIKey(
        name="Key 2",
        litellm_token="token2",
        litellm_api_url=test_region.litellm_api_url,
        team_id=test_team.id,
        owner_id=None,  # Service key
        region_id=test_region.id
    )
    db.add(key1)
    db.add(key2)
    db.commit()

    # Get budget duration from limit service (as the code does)
    limit_service = LimitService(db)
    days_left, _, _ = limit_service.get_token_restrictions(test_team.id)
    budget_duration = f"{days_left}d"

    # Set up the mock instance - track calls across threads using a thread-safe list
    import threading
    captured_calls = []
    call_lock = threading.Lock()

    async def mock_update_budget(*args, **kwargs):
        with call_lock:
            captured_calls.append((args, kwargs))

    def create_mock_instance(*args, **kwargs):
        mock_instance = AsyncMock()
        mock_instance.update_budget = mock_update_budget
        return mock_instance
    mock_litellm_class.side_effect = create_mock_instance

    # Update the team's budget limit via API
    response = client.put(
        "/limits/overwrite",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "owner_type": "team",
            "owner_id": test_team.id,
            "resource": "max_budget",
            "limit_type": "data_plane",
            "unit": "dollar",
            "max_value": 150.0
        }
    )

    assert response.status_code == 200

    # Wait for the background thread to execute (propagation happens in ThreadPoolExecutor)
    # Wait up to 5 seconds for completion, checking every 100ms
    max_wait = 50
    waited = 0
    while waited < max_wait:
        time.sleep(0.1)
        waited += 1
        with call_lock:
            if len(captured_calls) >= 2:
                break

    # Verify that update_budget was called for both keys with the new budget
    with call_lock:
        assert len(captured_calls) == 2, f"Expected 2 calls but got {len(captured_calls)}. Calls: {captured_calls}"

    # Verify the calls were made with correct parameters
    called_tokens = set()
    with call_lock:
        for args, kwargs in captured_calls:
            # update_budget may be called with positional or keyword arguments
            litellm_token = args[0] if args and len(args) > 0 else kwargs.get("litellm_token")
            budget_duration_arg = args[1] if args and len(args) > 1 else kwargs.get("budget_duration")
            budget_amount = kwargs.get("budget_amount")

            assert litellm_token is not None, f"Expected litellm_token but got args={args}, kwargs={kwargs}"
            called_tokens.add(litellm_token)
            assert budget_duration_arg == budget_duration, f"Expected budget_duration {budget_duration} but got {budget_duration_arg}"
            assert budget_amount == 150.0, f"Expected budget_amount 150.0 but got {budget_amount}"

    # Verify both keys were updated
    assert "token1" in called_tokens, f"Expected token1 in {called_tokens}"
    assert "token2" in called_tokens, f"Expected token2 in {called_tokens}"
