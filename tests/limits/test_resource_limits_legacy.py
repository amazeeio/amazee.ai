import pytest
from datetime import datetime, UTC
from fastapi import HTTPException
from app.db.models import DBUser, DBTeamProduct
from app.core.limit_service import LimitService


def test_legacy_resource_limits_integration(db, test_team, test_product):
    """Test legacy resource limits integration with new limit service"""
    # This test ensures backward compatibility with the legacy resource limits system
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Test that the legacy system still works
    limit_service = LimitService(db)

    # Test user limit check
    limit_service.check_team_user_limit(test_team.id)

    # Test key limit check
    limit_service.check_key_limits(test_team.id, None)

    # Test vector DB limit check
    limit_service.check_vector_db_limits(test_team.id)


def test_legacy_fallback_behavior(db, test_team, test_product):
    """Test that legacy fallback behavior still works correctly"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    limit_service = LimitService(db)

    # Test that fallback creates limits when none exist
    # This should not raise an exception
    limit_service.check_team_user_limit(test_team.id)
    limit_service.check_key_limits(test_team.id, None)
    limit_service.check_vector_db_limits(test_team.id)

    # Verify limits were created
    team_limits = limit_service.get_team_limits(test_team)
    assert len(team_limits) > 0


def test_legacy_error_messages(db, test_team, test_product):
    """Test that legacy error messages are preserved"""
    # Add product to team
    team_product = DBTeamProduct(
        team_id=test_team.id,
        product_id=test_product.id
    )
    db.add(team_product)
    db.commit()

    # Create users up to the limit
    for i in range(test_product.user_count):
        user = DBUser(
            email=f"user{i}@example.com",
            hashed_password="hashed_password",
            is_active=True,
            is_admin=False,
            role="user",
            team_id=test_team.id,
            created_at=datetime.now(UTC)
        )
        db.add(user)
    db.commit()

    # Test that the correct error message is returned
    with pytest.raises(HTTPException) as exc_info:
        limit_service = LimitService(db)
        limit_service.check_team_user_limit(test_team.id)
    assert exc_info.value.status_code == 402
    assert f"Team has reached the maximum user limit of {test_product.user_count} users" in str(exc_info.value.detail)
