import pytest
from datetime import datetime, UTC, timedelta
from fastapi import HTTPException
from jose import jwt
from app.core.worker import generate_team_admin_token, generate_pricing_url
from app.core.config import settings
from app.db.models import DBUser, DBTeam
from app.core.security import create_access_token
from unittest.mock import patch, MagicMock

def test_generate_team_admin_token(db, test_team, test_team_admin):
    """Test generating a JWT token for a team admin"""
    # Generate token
    token = generate_team_admin_token(db, test_team)

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

def test_generate_team_admin_token_no_admin(db, test_team):
    """Test generating a JWT token when no admin exists"""
    # Ensure no admin users exist
    db.query(DBUser).filter(
        DBUser.team_id == test_team.id,
        DBUser.role == "admin"
    ).delete()
    db.commit()

    # Attempt to generate token
    with pytest.raises(ValueError, match=f"No admin user found for team {test_team.name}"):
        generate_team_admin_token(db, test_team)

def test_generate_pricing_url(db, test_team, test_team_admin):
    """Test generating a pricing URL with JWT token"""
    # Generate URL
    url = generate_pricing_url(db, test_team)

    # Verify URL structure
    assert url.startswith(settings.frontend_route)
    assert "/upgrade" in url
    assert "token=" in url

    # Extract and verify token
    token = url.split("token=")[1]
    decoded = jwt.decode(token, settings.SECRET_KEY, algorithms=[settings.ALGORITHM])
    assert decoded["sub"] == test_team_admin.email

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
@patch('app.api.auth.DynamoDBService')
def test_validate_jwt_expired_token_query_param(mock_dynamodb, mock_ses, client, test_team_admin):
    """Test validating an expired JWT token provided as query parameter"""
    # Mock DynamoDB service
    mock_dynamodb_instance = mock_dynamodb.return_value
    mock_dynamodb_instance.write_validation_code = MagicMock()

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
    assert data["detail"] == "Token expired. A new validation code has been sent to your email."

    # Verify services were called
    mock_ses_instance.send_email.assert_called_once()
    mock_dynamodb_instance.write_validation_code.assert_called_once()

@patch('app.api.auth.SESService')
@patch('app.api.auth.DynamoDBService')
def test_validate_jwt_expired_token_auth_header(mock_dynamodb, mock_ses, client, test_team_admin):
    """Test validating an expired JWT token provided in Authorization header"""
    # Mock DynamoDB service
    mock_dynamodb_instance = mock_dynamodb.return_value
    mock_dynamodb_instance.write_validation_code = MagicMock()

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
    assert data["detail"] == "Token expired. A new validation code has been sent to your email."

    # Verify services were called
    mock_ses_instance.send_email.assert_called_once()
    mock_dynamodb_instance.write_validation_code.assert_called_once()

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