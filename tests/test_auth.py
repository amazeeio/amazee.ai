import pytest
from unittest.mock import patch, MagicMock
from app.api.auth import generate_validation_token

@pytest.fixture
def mock_dynamodb():
    """Fixture to mock DynamoDB service"""
    with patch('app.api.auth.DynamoDBService') as mock:
        mock_instance = MagicMock()
        mock.return_value = mock_instance
        yield mock_instance

@pytest.fixture
def mock_ses():
    """Fixture to mock SES service"""
    with patch('app.api.auth.SESService') as mock:
        mock_instance = MagicMock()
        mock.return_value = mock_instance
        yield mock_instance

def test_login_success(client, test_user):
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

def test_login_wrong_password(client, test_user):
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "wrongpassword"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]

def test_login_nonexistent_user(client):
    response = client.post(
        "/auth/login",
        data={"username": "nonexistent@example.com", "password": "testpassword"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]

def test_get_current_user(client, test_token):
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == "test@example.com"
    assert user_data["is_active"] is True
    assert user_data["is_admin"] is False

def test_get_current_user_invalid_token(client):
    response = client.get(
        "/auth/me",
        headers={"Authorization": "Bearer invalid_token"}
    )
    assert response.status_code == 401
    assert response.json()["detail"] == "Could not validate credentials"

def test_register_new_user(client):
    response = client.post(
        "/auth/register",
        json={
            "email": "newuser@example.com",
            "password": "newpassword123"
        }
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == "newuser@example.com"
    assert user_data["is_active"] is True
    assert user_data["is_admin"] is False

def test_register_existing_user(client, test_user):
    response = client.post(
        "/auth/register",
        json={
            "email": test_user.email,
            "password": "newpassword123"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

def test_update_password_for_user_with_password(client, test_user):
    # First login to get a token
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    # Update the password with current password
    response = client.put(
        "/auth/me/update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "testpassword",
            "new_password": "newpassword123"
        }
    )
    assert response.status_code == 200

    # Verify the new password works
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "newpassword123"}
    )
    assert response.status_code == 200
    assert "access_token" in response.json()

    # Verify old password no longer works
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 401
    assert "Incorrect email or password" in response.json()["detail"]

def test_update_password_fails_without_current_password(client, test_user):
    # First login to get a token
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    # Attempt to update password without current password
    response = client.put(
        "/auth/me/update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "new_password": "anotherpassword"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Current password is required to update password"

def test_update_password_fails_with_wrong_current_password(client, test_user):
    # First login to get a token
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    # Attempt to update password with wrong current password
    response = client.put(
        "/auth/me/update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "wrongpassword",
            "new_password": "anotherpassword"
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Incorrect password"

def test_update_email_to_existing_email_fails(client, test_user, test_admin):
    # First login to get a token for test_user
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200
    token = response.json()["access_token"]

    # Attempt to update email to test_admin's email
    response = client.put(
        "/auth/me/update",
        headers={"Authorization": f"Bearer {token}"},
        json={
            "current_password": "testpassword",
            "email": test_admin.email
        }
    )
    assert response.status_code == 400
    assert response.json()["detail"] == "Email already registered"

def test_generate_validation_token(client, mock_dynamodb):
    email = "test@example.com"
    code = generate_validation_token(email)

    # Verify the code is 8 characters and alphanumeric
    assert len(code) == 8
    assert code.isalnum()
    assert code.isupper()

    # Verify DynamoDB service was called correctly
    mock_dynamodb.write_validation_code.assert_called_once()
    call_args = mock_dynamodb.write_validation_code.call_args
    assert call_args[0][0] == email  # First argument should be the email
    assert len(call_args[0][1]) == 8  # Second argument should be an 8-character code

def test_validate_email_success_json(client, mock_dynamodb, mock_ses):
    email = "test@example.com"

    # Test with JSON data
    response = client.post(
        "/auth/validate-email",
        json={"email": email}
    )

    # Verify the JSON response
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "code" not in data

    # Verify DynamoDB service was called correctly
    mock_dynamodb.write_validation_code.assert_called_once()
    call_args = mock_dynamodb.write_validation_code.call_args
    assert call_args[0][0] == email  # First argument should be the email
    assert len(call_args[0][1]) == 8  # Second argument should be an 8-character code

    # Verify SES service was called correctly
    mock_ses.send_email.assert_called_once()
    ses_call_args = mock_ses.send_email.call_args
    assert ses_call_args[1]['to_addresses'] == [email]  # Verify recipient
    assert ses_call_args[1]['template_name'] == 'new-user-code'  # Verify template used
    assert 'code' in ses_call_args[1]['template_data']  # Verify code is included in template data
    assert len(ses_call_args[1]['template_data']['code']) == 8  # Verify code length

def test_validate_email_success_form(client, mock_dynamodb, mock_ses):
    email = "test@example.com"

    # Test with form data
    response = client.post(
        "/auth/validate-email",
        data={"email": email}
    )

    # Verify the form data response
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "code" not in data

    # Verify DynamoDB service was called correctly
    mock_dynamodb.write_validation_code.assert_called_once()
    call_args = mock_dynamodb.write_validation_code.call_args
    assert call_args[0][0] == email  # First argument should be the email
    assert len(call_args[0][1]) == 8  # Second argument should be an 8-character code

    # Verify SES service was called correctly
    mock_ses.send_email.assert_called_once()
    ses_call_args = mock_ses.send_email.call_args
    assert ses_call_args[1]['to_addresses'] == [email]  # Verify recipient
    assert ses_call_args[1]['template_name'] == 'new-user-code'  # Verify template used
    assert 'code' in ses_call_args[1]['template_data']  # Verify code is included in template data
    assert len(ses_call_args[1]['template_data']['code']) == 8  # Verify code length

def test_validate_email_invalid_format(client, mock_dynamodb):
    invalid_emails = [
        "notanemail",
        "missing@domain",
        "@nodomain",
        "noat.com",
        "special@chars!.com"
    ]

    for email in invalid_emails:
        response = client.post(
            "/auth/validate-email",
            json={"email": email}
        )

        # Verify the response
        assert response.status_code == 400
        data = response.json()
        assert "detail" in data
        assert "Invalid email format" in data["detail"]

        # Verify DynamoDB was not called
        mock_dynamodb.write_validation_code.assert_not_called()

def test_sign_in_success(client, test_user, mock_dynamodb):
    # First, generate a validation code
    email = test_user.email
    code = "TESTCODE"

    # Mock the read_validation_code to return our test code
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': code,
        'ttl': 1234567890
    }

    # Test with JSON data
    response = client.post(
        "/auth/sign-in",
        json={"username": email, "verification_code": code}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify DynamoDB service was called correctly
    mock_dynamodb.read_validation_code.assert_called_once_with(email)

def test_sign_in_success_form(client, test_user, mock_dynamodb):
    # First, generate a validation code
    email = test_user.email
    code = "TESTCODE"

    # Mock the read_validation_code to return our test code
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': code,
        'ttl': 1234567890
    }

    # Test with form data
    response = client.post(
        "/auth/sign-in",
        data={"username": email, "verification_code": code}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify DynamoDB service was called correctly
    mock_dynamodb.read_validation_code.assert_called_once_with(email)

def test_sign_in_wrong_code(client, test_user, mock_dynamodb):
    # First, generate a validation code
    email = test_user.email
    code = "TESTCODE"

    # Mock the read_validation_code to return a different code
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': 'DIFFERENT',
        'ttl': 1234567890
    }

    # Test with wrong code
    response = client.post(
        "/auth/sign-in",
        json={"username": email, "verification_code": code}
    )

    # Verify the response
    assert response.status_code == 401
    assert "Incorrect email or verification code" in response.json()["detail"]

    # Verify DynamoDB service was called correctly
    mock_dynamodb.read_validation_code.assert_called_once_with(email)

def test_sign_in_missing_data(client):
    # Test with missing data
    response = client.post(
        "/auth/sign-in",
        json={}
    )

    # Verify the response
    assert response.status_code == 400
    assert "Invalid sign in data" in response.json()["detail"]

def test_sign_in_new_user_success(client, mock_dynamodb):
    # Use a hardcoded validation code since we're mocking the response anyway
    email = "newuser@example.com"
    code = "TESTCODE"

    # Mock the read_validation_code to return our test code
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': code,
        'ttl': 1234567890
    }

    # Test with JSON data
    response = client.post(
        "/auth/sign-in",
        json={"username": email, "verification_code": code}
    )

    # Verify the response
    assert response.status_code == 200
    data = response.json()
    assert "access_token" in data
    assert data["token_type"] == "bearer"

    # Verify DynamoDB service was called correctly
    mock_dynamodb.read_validation_code.assert_called_once_with(email)

    # Get the token for subsequent requests
    token = data["access_token"]

    # Verify the user was created and has correct role
    response = client.get(
        "/auth/me",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    user_data = response.json()
    assert user_data["email"] == email
    assert user_data["is_active"] is True
    assert user_data["role"] == "admin"  # User should have admin role
    assert user_data["team_id"] is not None  # User should have a team

    # Verify the team was created correctly
    response = client.get(
        f"/teams/{user_data['team_id']}",
        headers={"Authorization": f"Bearer {token}"}
    )
    assert response.status_code == 200
    team_data = response.json()
    assert team_data["admin_email"] == email
    assert team_data["is_active"] is True
    assert len(team_data["users"]) == 1
    team_user = team_data["users"][0]
    assert team_user["email"] == email
    assert team_user["role"] == "admin"  # User should have admin role in the team

def test_create_token_basic(client, test_user, test_token):
    """
    Given a regular user
    When the user creates an API token
    Then the token should be created successfully and associated with the user
    """
    # Create token
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "Test Token"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Test Token"
    assert data["user_id"] == test_user.id
    assert "token" in data

def test_list_tokens_basic(client, test_user, test_token):
    """
    Given a regular user with existing tokens
    When the user lists their API tokens
    Then the tokens should be returned successfully
    """
    # Create a token first
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "Test Token"
        }
    )
    assert response.status_code == 200

    # List tokens
    response = client.get(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Test Token"
    assert data[0]["user_id"] == test_user.id

def test_create_token_system_admin_for_other_user(client, test_admin, test_user, admin_token):
    """
    Given a system administrator and a regular user
    When the system admin creates an API token for the regular user
    Then the token should be created successfully and associated with the regular user
    """
    # Create token for another user
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Admin Created Token",
            "user_id": test_user.id
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Admin Created Token"
    assert data["user_id"] == test_user.id
    assert "token" in data

def test_create_token_system_admin_for_other_user_invalid_user_id(client, test_admin, admin_token):
    """
    Given a system administrator
    When the system admin tries to create an API token for a non-existent user
    Then the request should fail with a 404 error
    """
    # Try to create token for non-existent user
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Admin Created Token",
            "user_id": 99999  # Non-existent user ID
        }
    )
    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]

def test_create_token_regular_user_for_other_user_fails(client, test_user, test_admin, test_token):
    """
    Given a regular user and a system administrator
    When the regular user tries to create an API token for the system admin
    Then the request should fail with a 403 error
    """
    # Try to create token for another user (should fail)
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "User Created Token",
            "user_id": test_admin.id
        }
    )
    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_list_tokens_system_admin_for_other_user(client, test_admin, test_user, admin_token):
    """
    Given a system administrator and a regular user with existing tokens
    When the system admin lists API tokens for the regular user
    Then the tokens should be returned successfully
    """
    # First create a token for the user (as admin)
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Admin Created Token",
            "user_id": test_user.id
        }
    )
    assert response.status_code == 200

    # List tokens for the user
    response = client.get(
        f"/auth/token?user_id={test_user.id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Admin Created Token"
    assert data[0]["user_id"] == test_user.id

def test_list_tokens_system_admin_for_other_user_invalid_user_id(client, test_admin, admin_token):
    """
    Given a system administrator
    When the system admin tries to list API tokens for a non-existent user
    Then the request should fail with a 404 error
    """
    # Try to list tokens for non-existent user
    response = client.get(
        "/auth/token?user_id=99999",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert "User not found" in response.json()["detail"]

def test_list_tokens_regular_user_for_other_user_fails(client, test_user, test_admin, test_token):
    """
    Given a regular user and a system administrator
    When the regular user tries to list API tokens for the system admin
    Then the request should fail with a 403 error
    """
    # Try to list tokens for another user (should fail)
    response = client.get(
        f"/auth/token?user_id={test_admin.id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 403
    assert "Not authorized to perform this action" in response.json()["detail"]

def test_create_token_system_admin_without_user_id_creates_for_self(client, test_admin, admin_token):
    """
    Given a system administrator
    When the system admin creates an API token without specifying user_id
    Then the token should be created for the system admin themselves
    """
    # Create token without user_id (should create for self)
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Admin Self Token"
        }
    )
    assert response.status_code == 200
    data = response.json()
    assert data["name"] == "Admin Self Token"
    assert data["user_id"] == test_admin.id
    assert "token" in data

def test_list_tokens_system_admin_without_user_id_lists_own_tokens(client, test_admin, admin_token):
    """
    Given a system administrator with existing tokens
    When the system admin lists API tokens without specifying user_id
    Then the system admin's own tokens should be returned
    """
    # Create a token for the admin
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {admin_token}"},
        json={
            "name": "Admin Self Token"
        }
    )
    assert response.status_code == 200

    # List tokens without user_id (should list own tokens)
    response = client.get(
        "/auth/token",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 1
    assert data[0]["name"] == "Admin Self Token"
    assert data[0]["user_id"] == test_admin.id

def test_delete_token_basic(client, test_user, test_token):
    """
    Given a regular user with an existing token
    When the user deletes their API token
    Then the token should be deleted successfully
    """
    # Create a token first
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "Test Token"
        }
    )
    assert response.status_code == 200
    token_data = response.json()
    token_id = token_data["id"]

    # Delete the token
    response = client.delete(
        f"/auth/token/{token_id}",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    assert response.json()["message"] == "Token deleted successfully"

    # Verify token is deleted by listing tokens
    response = client.get(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"}
    )
    assert response.status_code == 200
    data = response.json()
    assert len(data) == 0

def test_delete_token_system_admin_for_other_user(client, test_admin, test_user, admin_token, test_token):
    """
    Given a system administrator and a regular user with an existing token
    When the system admin tries to delete the user's API token
    Then the request should fail as this functionality is not implemented
    """
    # Create a token for the user
    response = client.post(
        "/auth/token",
        headers={"Authorization": f"Bearer {test_token}"},
        json={
            "name": "User Token"
        }
    )
    assert response.status_code == 200
    token_data = response.json()
    token_id = token_data["id"]

    # Admin tries to delete the user's token (should fail as not implemented)
    response = client.delete(
        f"/auth/token/{token_id}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 404
    assert response.json()["detail"] == "Token not found"

def test_login_cookie_expiration_regular_user(client, test_user):
    """
    Given a regular user
    When the user logs in successfully
    Then the cookie should expire in 30 minutes (1800 seconds)
    """
    response = client.post(
        "/auth/login",
        data={"username": test_user.email, "password": "testpassword"}
    )
    assert response.status_code == 200

    # Check that the cookie is set with 30-minute expiration
    cookies = response.cookies
    assert "access_token" in cookies

    # Check the Set-Cookie header for max-age
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=1800" in set_cookie_header or "max-age=1800" in set_cookie_header

def test_sign_in_cookie_expiration_regular_user(client, test_user, mock_dynamodb):
    """
    Given a regular user
    When the user signs in with verification code
    Then the cookie should expire in 30 minutes (1800 seconds)
    """
    # Mock the validation code
    email = test_user.email
    code = "TESTCODE"
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': code,
        'ttl': 1234567890
    }

    response = client.post(
        "/auth/sign-in",
        json={"username": email, "verification_code": code}
    )
    assert response.status_code == 200

    # Check that the cookie is set with 30-minute expiration
    cookies = response.cookies
    assert "access_token" in cookies

    # Check the Set-Cookie header for max-age
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=1800" in set_cookie_header or "max-age=1800" in set_cookie_header

def test_sign_in_cookie_expiration_system_admin(client, test_admin, mock_dynamodb):
    """
    Given a system administrator
    When the system admin signs in with verification code
    Then the cookie should expire in 8 hours (28800 seconds)
    """
    # Mock the validation code
    email = test_admin.email
    code = "TESTCODE"
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': code,
        'ttl': 1234567890
    }

    response = client.post(
        "/auth/sign-in",
        json={"username": email, "verification_code": code}
    )
    assert response.status_code == 200

    # Check that the cookie is set with 8-hour expiration
    cookies = response.cookies
    assert "access_token" in cookies

    # Check the Set-Cookie header for max-age
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=28800" in set_cookie_header or "max-age=28800" in set_cookie_header

def test_sign_in_new_user_cookie_expiration(client, mock_dynamodb):
    """
    Given a new user signing in for the first time
    When the user signs in with verification code
    Then the cookie should expire in 30 minutes (1800 seconds) since they are not a system admin
    """
    # Mock the validation code
    email = "newuser@example.com"
    code = "TESTCODE"
    mock_dynamodb.read_validation_code.return_value = {
        'email': email,
        'code': code,
        'ttl': 1234567890
    }

    response = client.post(
        "/auth/sign-in",
        json={"username": email, "verification_code": code}
    )
    assert response.status_code == 200

    # Check that the cookie is set with 30-minute expiration (new users are not system admins)
    cookies = response.cookies
    assert "access_token" in cookies

    # Check the Set-Cookie header for max-age
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=1800" in set_cookie_header or "max-age=1800" in set_cookie_header

def test_validate_jwt_cookie_expiration_regular_user(client, test_user, test_token):
    """
    Given a regular user with a valid JWT token
    When the user validates their JWT token
    Then the cookie should expire in 30 minutes (1800 seconds)
    """
    response = client.get(
        f"/auth/validate-jwt?token={test_token}"
    )
    assert response.status_code == 200

    # Check that the cookie is set with 30-minute expiration
    cookies = response.cookies
    assert "access_token" in cookies

    # Check the Set-Cookie header for max-age
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=1800" in set_cookie_header or "max-age=1800" in set_cookie_header

def test_validate_jwt_cookie_expiration_system_admin(client, test_admin, admin_token):
    """
    Given a system administrator with a valid JWT token
    When the system admin validates their JWT token
    Then the cookie should expire in 8 hours (28800 seconds)
    """
    response = client.get(
        f"/auth/validate-jwt?token={admin_token}"
    )
    assert response.status_code == 200

    # Check that the cookie is set with 8-hour expiration
    cookies = response.cookies
    assert "access_token" in cookies

    # Check the Set-Cookie header for max-age
    set_cookie_header = response.headers.get("set-cookie", "")
    assert "Max-Age=28800" in set_cookie_header or "max-age=28800" in set_cookie_header