import pytest
from unittest.mock import AsyncMock, patch
from sqlalchemy.orm import Session
from app.core.worker import monitor_trial_users
from app.db.models import DBTeam, DBUser, DBLimitedResource, DBPrivateAIKey, DBRegion
from app.schemas.limits import ResourceType, OwnerType, LimitType, UnitType, LimitSource
from app.core.config import settings

@pytest.fixture
def trial_team(db: Session):
    team = DBTeam(
        name="AI Trial Team",
        admin_email=settings.AI_TRIAL_TEAM_EMAIL,
        is_active=True
    )
    db.add(team)
    db.commit()
    return team

@pytest.fixture
def trial_user(db: Session, trial_team: DBTeam):
    user = DBUser(
        email="trial-user@example.com",
        team_id=trial_team.id,
        is_active=True,
        role="user"
    )
    db.add(user)
    db.commit()
    return user

@pytest.fixture
def trial_region(db: Session):
    region = DBRegion(
        name="test-trial-region",
        litellm_api_url="http://mock-litellm",
        litellm_api_key="mock-key",
        is_active=True
    )
    db.add(region)
    db.commit()
    return region

@pytest.fixture
def trial_key(db: Session, trial_user: DBUser, trial_region: DBRegion, trial_team: DBTeam):
    key = DBPrivateAIKey(
        owner_id=trial_user.id,
        team_id=trial_team.id,
        region_id=trial_region.id,
        litellm_token="mock-token",
        name="Trial Key"
    )
    db.add(key)
    db.commit()
    return key

@pytest.fixture
def user_budget_limit(db: Session, trial_user: DBUser):
    limit = DBLimitedResource(
        resource=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        owner_type=OwnerType.USER,
        owner_id=trial_user.id,
        max_value=10.0,
        current_value=0.0,
        limited_by=LimitSource.MANUAL,
        set_by="test"
    )
    db.add(limit)
    db.commit()
    return limit

@pytest.fixture
def mock_litellm():
    """Fixture to mock LiteLLMService."""
    with patch('app.core.worker.LiteLLMService', autospec=True) as MockLiteLLM:
        mock_instance = MockLiteLLM.return_value
        mock_instance.update_key_duration = AsyncMock()
        yield mock_instance

@pytest.mark.asyncio
async def test_monitor_trial_users_no_overage(db, trial_team, trial_user, trial_key, user_budget_limit, mock_litellm):
    """Test that users within budget are not affected."""
    # usage is 5.0, max is 10.0
    user_budget_limit.current_value = 5.0
    db.commit()

    await monitor_trial_users(db)
    
    # Verify user is still active
    db.refresh(trial_user)
    assert trial_user.is_active is True
    
    # Verify LiteLLM was not called
    assert mock_litellm.update_key_duration.call_count == 0

@pytest.mark.asyncio
async def test_monitor_trial_users_with_overage(db, trial_team, trial_user, trial_key, user_budget_limit, mock_litellm):
    """Test that users over budget are disabled and keys expired."""
    # usage is 10.0, max is 10.0 (limit reached)
    user_budget_limit.current_value = 10.0
    db.commit()

    await monitor_trial_users(db)
    
    # Verify user is deactivated
    db.refresh(trial_user)
    assert trial_user.is_active is False
    
    # Verify LiteLLM called with 0d duration
    mock_litellm.update_key_duration.assert_called_once_with("mock-token", "0d")

@pytest.mark.asyncio
async def test_monitor_trial_users_skips_admin(db, trial_team, mock_litellm):
    """Test that admin user is skipped even if over budget."""
    admin_user = DBUser(
        email="admin@example.com",
        team_id=trial_team.id,
        is_active=True,
        role="admin"
    )
    db.add(admin_user)
    db.commit()

    # Even if we add a limit
    limit = DBLimitedResource(
        resource=ResourceType.BUDGET,
        limit_type=LimitType.DATA_PLANE,
        unit=UnitType.DOLLAR,
        owner_type=OwnerType.USER,
        owner_id=admin_user.id,
        max_value=10.0,
        current_value=15.0,
        limited_by=LimitSource.MANUAL,
        set_by="test"
    )
    db.add(limit)
    db.commit()

    await monitor_trial_users(db)
    
    db.refresh(admin_user)
    assert admin_user.is_active is True
    assert mock_litellm.update_key_duration.call_count == 0