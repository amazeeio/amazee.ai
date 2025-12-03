import pytest
from datetime import datetime, UTC, timedelta
from jose import jwt
from app.core.worker import generate_token, generate_pricing_url, get_team_admin_email
from app.core.config import settings
from app.db.models import DBUser, DBTeam
from app.core.security import create_access_token
from unittest.mock import patch

def test_generate_team_admin_token(db, test_team, test_team_admin):
    """Test generating a JWT token for a team admin"""
    # Generate token
    token = generate_token(test_team_admin.email)

    # Verify token structure
    decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert decoded["sub"] == test_team_admin.email
    assert "exp" in decoded

    # Verify expiration is set correctly (1 day)
    exp_timestamp = decoded["exp"]
    exp_datetime = datetime.fromtimestamp(exp_timestamp, tz=UTC)
    expected_exp = datetime.now(UTC) + timedelta(days=1)
    # Allow 1 second tolerance for test execution time
    assert abs((exp_datetime - expected_exp).total_seconds()) <= 1

def test_generate_pricing_url(db, test_team, test_team_admin):
    """Test generating a pricing URL with JWT token"""
    # Get admin email first
    admin_email = get_team_admin_email(db, test_team)

    # Generate URL
    url = generate_pricing_url(admin_email)

    # Verify URL structure
    assert url.startswith(settings.frontend_route)
    assert "/upgrade" in url
    assert "token=" in url

    # Extract and verify token
    token = url.split("token=")[1]
    decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert decoded["sub"] == admin_email

def test_validate_jwt_valid_token_query_param(client, test_team_admin):
    """Test validating a valid JWT token provided as query parameter"""
    # Create a valid token
    token = create_access_token(data={"sub": test_team_admin.email})

    # Validate token via query parameter
    response = client.get(f"/auth/validate-jwt?token={token}")
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify new token is valid
    new_token = data["access_token"]
    decoded = jwt.decode(new_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert decoded["sub"] == test_team_admin.email

def test_validate_jwt_valid_token_auth_header(client, test_team_admin):
    """Test validating a valid JWT token provided in Authorization header"""
    # Create a valid token
    token = create_access_token(data={"sub": test_team_admin.email})

    # Validate token via Authorization header
    response = client.get("/auth/validate-jwt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify new token is valid
    new_token = data["access_token"]
    decoded = jwt.decode(new_token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert decoded["sub"] == test_team_admin.email

@patch('app.api.auth.SESService')
def test_validate_jwt_expired_token_query_param(mock_ses, client, test_team_admin):
    """Test validating an expired JWT token provided as query parameter"""
    # Mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email.return_value = True

    # Create an expired token by using a negative timedelta
    token = create_access_token(
        data={"sub": test_team_admin.email},
        expires_delta=timedelta(seconds=-1)  # Set expiration to 1 second in the past
    )

    # Validate token via query parameter
    response = client.get(f"/auth/validate-jwt?token={token}")
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Token expired. A new validation URL has been sent to your email."

    # Verify SES service was called (DynamoDB is not used for URL validation)
    mock_ses_instance.send_email.assert_called_once()
    # Verify the template data contains validation_url
    call_args = mock_ses_instance.send_email.call_args
    assert call_args[1]['template_data']['validation_url'] is not None

@patch('app.api.auth.SESService')
def test_validate_jwt_expired_token_auth_header(mock_ses, client, test_team_admin):
    """Test validating an expired JWT token provided in Authorization header"""
    # Mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email.return_value = True

    # Create an expired token by using a negative timedelta
    token = create_access_token(
        data={"sub": test_team_admin.email},
        expires_delta=timedelta(seconds=-1)  # Set expiration to 1 second in the past
    )

    # Validate token via Authorization header
    response = client.get("/auth/validate-jwt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert data["detail"] == "Token expired. A new validation URL has been sent to your email."

    # Verify SES service was called (DynamoDB is not used for URL validation)
    mock_ses_instance.send_email.assert_called_once()
    # Verify the template data contains validation_url
    call_args = mock_ses_instance.send_email.call_args
    assert call_args[1]['template_data']['validation_url'] is not None

def test_validate_jwt_invalid_token_query_param(client):
    """Test validating an invalid JWT token provided as query parameter"""
    # Create an invalid token
    token = "invalid.token.here"

    # Validate token via query parameter
    response = client.get(f"/auth/validate-jwt?token={token}")
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

def test_validate_jwt_invalid_token_auth_header(client):
    """Test validating an invalid JWT token provided in Authorization header"""
    # Create an invalid token
    token = "invalid.token.here"

    # Validate token via Authorization header
    response = client.get("/auth/validate-jwt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

def test_validate_jwt_missing_token(client):
    """Test validating without providing a token"""
    # Validate without token
    response = client.get("/auth/validate-jwt")
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

def test_validate_jwt_invalid_auth_header_format(client):
    """Test validating with an invalid Authorization header format"""
    # Test with missing Bearer prefix
    response = client.get("/auth/validate-jwt", headers={"Authorization": "invalid-token"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

    # Test with empty Authorization header
    response = client.get("/auth/validate-jwt", headers={"Authorization": ""})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

def test_validate_jwt_wrong_user_query_param(client):
    """Test validating a token for a non-existent user provided as query parameter"""
    # Create token for non-existent user
    token = create_access_token(data={"sub": "nonexistent@example.com"})

    # Validate token via query parameter
    response = client.get(f"/auth/validate-jwt?token={token}")
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

def test_validate_jwt_wrong_user_auth_header(client):
    """Test validating a token for a non-existent user provided in Authorization header"""
    # Create token for non-existent user
    token = create_access_token(data={"sub": "nonexistent@example.com"})

    # Validate token via Authorization header
    response = client.get("/auth/validate-jwt", headers={"Authorization": f"Bearer {token}"})
    assert response.status_code == 401
    data = response.json()
    assert "detail" in data
    assert "Could not validate credentials" in data["detail"]

@patch('app.api.auth.SESService')
def test_send_validation_url_existing_user(mock_ses, db, test_team_admin):
    """Test sending validation URL to a user"""
    from app.api.auth import send_validation_url

    # Mock SES service
    mock_ses_instance = mock_ses.return_value
    mock_ses_instance.send_email.return_value = True

    # Send validation URL
    send_validation_url(test_team_admin.email)

    # Verify SES service was called with correct parameters
    mock_ses_instance.send_email.assert_called_once()
    call_args = mock_ses_instance.send_email.call_args
    assert call_args[1]['to_addresses'] == [test_team_admin.email]
    assert call_args[1]['template_name'] == 'returning-user-url'
    assert 'validation_url' in call_args[1]['template_data']
    assert call_args[1]['template_data']['validation_url'] is not None

def test_get_team_admin_email_success(db, test_team, test_team_admin):
    """Test successfully getting admin email for a team"""
    # Call the function
    admin_email = get_team_admin_email(db, test_team)

    # Verify the result
    assert admin_email == test_team_admin.email

def test_get_team_admin_email_no_admin_found(db, test_team):
    """Test getting admin email when no admin user exists for the team"""
    # Ensure no admin users exist for this team
    db.query(DBUser).filter(
        DBUser.team_id == test_team.id,
        DBUser.role == "admin"
    ).delete()
    db.commit()

    # Attempt to get admin email and expect ValueError
    with pytest.raises(ValueError, match=f"No admin user found for team {test_team.name} \\(ID: {test_team.id}\\)"):
        get_team_admin_email(db, test_team)

def test_get_team_admin_email_multiple_admins_returns_first(db, test_team, test_team_admin):
    """Test getting admin email when multiple admin users exist (should return first)"""
    # Create a second admin user for the same team
    second_admin = DBUser(
        email="second_admin@test.com",
        hashed_password="hashed_password",
        team_id=test_team.id,
        role="admin"
    )
    db.add(second_admin)
    db.commit()

    # Call the function
    admin_email = get_team_admin_email(db, test_team)

    # Should return the first admin found (which could be either one)
    assert admin_email in [test_team_admin.email, second_admin.email]

def test_get_team_admin_email_other_roles_ignored(db, test_team, test_team_admin):
    """Test that users with other roles are ignored when looking for admin"""
    # Create a user with a different role for the same team
    regular_user = DBUser(
        email="regular_user@test.com",
        hashed_password="hashed_password",
        team_id=test_team.id,
        role="user"  # Not admin
    )
    db.add(regular_user)
    db.commit()

    # Call the function
    admin_email = get_team_admin_email(db, test_team)

    # Should still return the admin user, not the regular user
    assert admin_email == test_team_admin.email
    assert admin_email != regular_user.email

def test_get_team_admin_email_different_team_ignored(db, test_team, test_team_admin):
    """Test that users from different teams are ignored when looking for admin"""
    # Create a different team
    other_team = DBTeam(
        name="Other Team",
        admin_email="other_admin@test.com"
    )
    db.add(other_team)
    db.commit()

    # Create an admin user for the other team
    other_team_admin = DBUser(
        email="other_admin@test.com",
        hashed_password="hashed_password",
        team_id=other_team.id,
        role="admin"
    )
    db.add(other_team_admin)
    db.commit()

    # Call the function for the original team
    admin_email = get_team_admin_email(db, test_team)

    # Should return the admin from the original team, not the other team
    assert admin_email == test_team_admin.email
    assert admin_email != other_team_admin.email

def test_get_team_admin_email_team_without_users(db):
    """Test getting admin email for a team that has no users at all"""
    # Create a team with no users
    empty_team = DBTeam(
        name="Empty Team",
        admin_email="empty_admin@test.com"
    )
    db.add(empty_team)
    db.commit()

    # Attempt to get admin email and expect ValueError
    with pytest.raises(ValueError, match=f"No admin user found for team {empty_team.name} \\(ID: {empty_team.id}\\)"):
        get_team_admin_email(db, empty_team)