from unittest.mock import AsyncMock, patch

import pytest

from app.core.litellm_user_sync import (
    sync_add_user_to_team,
    sync_create_user_across_regions,
)
from app.db.models import DBUser


@patch("app.core.litellm_user_sync.LiteLLMService")
@pytest.mark.asyncio
async def test_sync_create_user_skips_trial_users(
    mock_litellm, db, test_team, test_region
):
    user = DBUser(email="trial-1775630556-f9c2b8a9@example.com", team_id=test_team.id)
    db.add(user)
    db.commit()
    db.refresh(user)

    await sync_create_user_across_regions(db=db, db_user=user, team_id=test_team.id)

    mock_litellm.assert_not_called()


@patch("app.core.litellm_user_sync.LiteLLMService")
@pytest.mark.asyncio
async def test_sync_create_user_calls_litellm_for_regular_users(
    mock_litellm, db, test_team, test_region
):
    user = DBUser(email="regular-user@example.com", team_id=test_team.id)
    db.add(user)
    db.commit()
    db.refresh(user)

    mock_service = AsyncMock()
    mock_litellm.return_value = mock_service

    await sync_create_user_across_regions(db=db, db_user=user, team_id=test_team.id)

    mock_litellm.assert_called_once_with(
        api_url=test_region.litellm_api_url, api_key=test_region.litellm_api_key
    )
    mock_service.create_user.assert_awaited_once_with(
        user_id=str(user.id), user_email=user.email, auto_create_key=False
    )


@patch("app.core.litellm_user_sync.LiteLLMService")
@pytest.mark.asyncio
async def test_sync_add_user_to_team_skips_trial_users(
    mock_litellm, db, test_team, test_region
):
    user = DBUser(email="trial-1775630556-f9c2b8a9@example.com", team_id=test_team.id)
    db.add(user)
    db.commit()
    db.refresh(user)

    await sync_add_user_to_team(db=db, db_user=user, team_id=test_team.id)

    mock_litellm.assert_not_called()
