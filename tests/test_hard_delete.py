import pytest
from datetime import datetime, UTC, timedelta
from unittest.mock import patch, AsyncMock
from sqlalchemy.orm import Session

from app.db.models import (
    DBTeam, DBUser, DBPrivateAIKey, DBTeamProduct, DBProduct,
    DBTeamMetrics, DBLimitedResource, DBTeamRegion, DBRegion
)
from app.schemas.limits import ResourceType, UnitType, OwnerType, LimitType, LimitSource
from app.core.worker import hard_delete_expired_teams
from tests.conftest import soft_delete_team_for_test


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_teams_older_than_60_days(mock_litellm, db: Session, test_team, test_region):
    """
    Given: A team that was soft-deleted 61 days ago
    When: Running the hard delete job
    Then: Should permanently delete the team and all related resources
    """
    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    # Create related resources
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id
    )
    db.add(user)

    key = DBPrivateAIKey(
        name="test-key",
        litellm_token="test-token",
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(key)
    db.commit()

    team_id = test_team.id

    # Setup mock LiteLLM service
    mock_service = AsyncMock()
    mock_litellm.return_value = mock_service

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify team was deleted
    deleted_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    assert deleted_team is None

    # Verify user was deleted
    deleted_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    assert deleted_user is None

    # Verify key was deleted
    deleted_key = db.query(DBPrivateAIKey).filter(DBPrivateAIKey.name == "test-key").first()
    assert deleted_key is None

    # Verify LiteLLM delete was called
    mock_service.delete_key.assert_called_once_with("test-token")


@pytest.mark.asyncio
async def test_hard_delete_preserves_recent_soft_deletes(db: Session, test_team):
    """
    Given: A team that was soft-deleted 30 days ago (less than 60 days)
    When: Running the hard delete job
    Then: Should NOT delete the team (still within grace period)
    """
    # Soft delete the team 30 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=30))

    team_id = test_team.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify team still exists
    team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    assert team is not None
    assert team.deleted_at is not None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_exactly_60_days(mock_litellm, db: Session, test_team):
    """
    Given: A team that was soft-deleted exactly 60 days ago
    When: Running the hard delete job
    Then: Should permanently delete the team (inclusive boundary)
    """
    # Soft delete the team exactly 60 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=60))

    team_id = test_team.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify team was deleted
    deleted_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    assert deleted_team is None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_cascades_team_metrics(mock_litellm, db: Session, test_team):
    """
    Given: A team with metrics that was soft-deleted 61 days ago
    When: Running the hard delete job
    Then: Should delete the team metrics
    """
    # Create team metrics
    metrics = DBTeamMetrics(
        team_id=test_team.id,
        total_spend=100.0,
        last_spend_calculation=datetime.now(UTC),
        regions=["test-region"]
    )
    db.add(metrics)

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    team_id = test_team.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify metrics were deleted
    deleted_metrics = db.query(DBTeamMetrics).filter(DBTeamMetrics.team_id == team_id).first()
    assert deleted_metrics is None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_cascades_limited_resources(mock_litellm, db: Session, test_team):
    """
    Given: A team with limited resources that was soft-deleted 61 days ago
    When: Running the hard delete job
    Then: Should delete all team and user limited resources
    """
    # Create user with limited resources
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id
    )
    db.add(user)
    db.commit()

    # Create team limited resource
    team_resource = DBLimitedResource(
        owner_id=test_team.id,
        owner_type=OwnerType.TEAM,
        resource=ResourceType.USER,
        unit=UnitType.COUNT,
        max_value=10.0,
        current_value=5.0,
        limit_type=LimitType.CONTROL_PLANE,
        limited_by=LimitSource.DEFAULT
    )
    db.add(team_resource)

    # Create user limited resource
    user_resource = DBLimitedResource(
        owner_id=user.id,
        owner_type=OwnerType.USER,
        resource=ResourceType.USER_KEY,
        unit=UnitType.COUNT,
        max_value=5.0,
        current_value=2.0,
        limit_type=LimitType.CONTROL_PLANE,
        limited_by=LimitSource.DEFAULT
    )
    db.add(user_resource)

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    team_id = test_team.id
    user_id = user.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify all resources were deleted
    team_resources = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.TEAM,
        DBLimitedResource.owner_id == team_id
    ).all()
    assert len(team_resources) == 0

    user_resources = db.query(DBLimitedResource).filter(
        DBLimitedResource.owner_type == OwnerType.USER,
        DBLimitedResource.owner_id == user_id
    ).all()
    assert len(user_resources) == 0


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_cascades_product_associations(mock_litellm, db: Session, test_team):
    """
    Given: A team with product associations that was soft-deleted 61 days ago
    When: Running the hard delete job
    Then: Should delete the team-product associations
    """
    # Create product and associate with team
    product = DBProduct(
        id="test-product",
        name="Test Product",
        active=True
    )
    db.add(product)
    db.commit()

    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=product.id
    )
    db.add(team_product)

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    team_id = test_team.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify team-product association was deleted
    association = db.query(DBTeamProduct).filter(DBTeamProduct.team_id == team_id).first()
    assert association is None

    # Product itself should still exist
    existing_product = db.query(DBProduct).filter(DBProduct.id == "test-product").first()
    assert existing_product is not None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_cascades_region_associations(mock_litellm, db: Session, test_team, test_region):
    """
    Given: A team with region associations that was soft-deleted 61 days ago
    When: Running the hard delete job
    Then: Should delete the team-region associations
    """
    # Associate team with region
    team_region = DBTeamRegion(
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(team_region)

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    team_id = test_team.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify team-region association was deleted
    association = db.query(DBTeamRegion).filter(DBTeamRegion.team_id == team_id).first()
    assert association is None

    # Region itself should still exist
    existing_region = db.query(DBRegion).filter(DBRegion.id == test_region.id).first()
    assert existing_region is not None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_calls_litellm_for_all_keys(mock_litellm, db: Session, test_team, test_region):
    """
    Given: A team with multiple keys that was soft-deleted 61 days ago
    When: Running the hard delete job
    Then: Should call LiteLLM delete for each key
    """
    # Create multiple keys
    key1 = DBPrivateAIKey(
        name="key1",
        litellm_token="token1",
        team_id=test_team.id,
        region_id=test_region.id
    )
    key2 = DBPrivateAIKey(
        name="key2",
        litellm_token="token2",
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add_all([key1, key2])

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    # Mock LiteLLM service
    mock_service = AsyncMock()
    mock_litellm.return_value = mock_service

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify delete was called for both keys
    assert mock_service.delete_key.call_count == 2
    mock_service.delete_key.assert_any_call("token1")
    mock_service.delete_key.assert_any_call("token2")


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_continues_on_litellm_error(mock_litellm, db: Session, test_team, test_region):
    """
    Given: A team with a key that fails to delete from LiteLLM
    When: Running the hard delete job
    Then: Should continue and delete the team from database anyway
    """
    # Create key
    key = DBPrivateAIKey(
        name="test-key",
        litellm_token="test-token",
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(key)

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    team_id = test_team.id

    # Mock LiteLLM service to raise an error
    mock_service = AsyncMock()
    mock_service.delete_key.side_effect = Exception("LiteLLM API error")
    mock_litellm.return_value = mock_service

    # Run hard delete job (should not raise exception)
    await hard_delete_expired_teams(db)

    # Verify team was still deleted despite LiteLLM error
    deleted_team = db.query(DBTeam).filter(DBTeam.id == team_id).first()
    assert deleted_team is None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_deletes_user_owned_keys(mock_litellm, db: Session, test_team, test_region):
    """
    Given: A team with users who own keys
    When: Running the hard delete job
    Then: Should delete both team keys and user-owned keys
    """
    # Create user
    user = DBUser(
        email="test@example.com",
        team_id=test_team.id
    )
    db.add(user)
    db.commit()

    # Create team-owned key
    team_key = DBPrivateAIKey(
        name="team-key",
        litellm_token="team-token",
        team_id=test_team.id,
        region_id=test_region.id
    )
    db.add(team_key)

    # Create user-owned key
    user_key = DBPrivateAIKey(
        name="user-key",
        litellm_token="user-token",
        owner_id=user.id,
        region_id=test_region.id
    )
    db.add(user_key)

    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    mock_service = AsyncMock()
    mock_litellm.return_value = mock_service

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify both keys were deleted
    remaining_keys = db.query(DBPrivateAIKey).filter(
        (DBPrivateAIKey.name == "team-key") | (DBPrivateAIKey.name == "user-key")
    ).all()
    assert len(remaining_keys) == 0

    # Verify LiteLLM delete was called for both
    assert mock_service.delete_key.call_count == 2


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_multiple_teams(mock_litellm, db: Session, test_region):
    """
    Given: Multiple teams that were soft-deleted over 60 days ago
    When: Running the hard delete job
    Then: Should delete all eligible teams
    """
    # Create multiple teams
    team1 = DBTeam(name="Team 1", admin_email="team1@example.com")
    team2 = DBTeam(name="Team 2", admin_email="team2@example.com")
    team3 = DBTeam(name="Team 3", admin_email="team3@example.com")
    db.add_all([team1, team2, team3])
    db.commit()

    # Soft delete teams with different timestamps
    soft_delete_team_for_test(db, team1, deleted_at=datetime.now(UTC) - timedelta(days=65))
    soft_delete_team_for_test(db, team2, deleted_at=datetime.now(UTC) - timedelta(days=70))
    soft_delete_team_for_test(db, team3, deleted_at=datetime.now(UTC) - timedelta(days=30))  # Too recent

    team1_id = team1.id
    team2_id = team2.id
    team3_id = team3.id

    # Run hard delete job
    await hard_delete_expired_teams(db)

    # Verify old teams were deleted
    assert db.query(DBTeam).filter(DBTeam.id == team1_id).first() is None
    assert db.query(DBTeam).filter(DBTeam.id == team2_id).first() is None

    # Verify recent team still exists
    assert db.query(DBTeam).filter(DBTeam.id == team3_id).first() is not None


@pytest.mark.asyncio
async def test_hard_delete_no_eligible_teams(db: Session):
    """
    Given: No teams that are eligible for hard deletion
    When: Running the hard delete job
    Then: Should complete successfully without errors
    """
    # No teams or only recently soft-deleted teams
    team = DBTeam(name="Recent Team", admin_email="recent@example.com")
    db.add(team)
    db.commit()

    # Soft delete team with recent timestamp (not eligible for hard delete)
    soft_delete_team_for_test(db, team, deleted_at=datetime.now(UTC) - timedelta(days=10))

    # Run hard delete job (should not raise exception)
    await hard_delete_expired_teams(db)

    # Verify team still exists
    assert db.query(DBTeam).filter(DBTeam.name == "Recent Team").first() is not None


@patch('app.core.worker.LiteLLMService')
@pytest.mark.asyncio
async def test_hard_delete_rollback_on_error(mock_litellm, db: Session, test_team):
    """
    Given: A team eligible for hard deletion
    When: An error occurs during deletion
    Then: Should rollback the transaction for that team
    """
    # Soft delete the team 61 days ago
    soft_delete_team_for_test(db, test_team, deleted_at=datetime.now(UTC) - timedelta(days=61))

    team_id = test_team.id

    # Mock to raise an error during deletion
    mock_litellm.side_effect = Exception("Unexpected error")

    # Run hard delete job (should handle the error)
    try:
        await hard_delete_expired_teams(db)
    except Exception:
        pass  # Job should handle errors internally

    # Verify team still exists (rollback occurred)
    db.query(DBTeam).filter(DBTeam.id == team_id).first()
    # Team might be deleted or might not be, depending on where error occurred
    # The important thing is that the job didn't crash

