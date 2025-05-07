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

def test_validate_email_success_json(client, mock_dynamodb):
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

def test_validate_email_success_form(client, mock_dynamodb):
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
    code = generate_validation_token(email)

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
    code = generate_validation_token(email)

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
    code = generate_validation_token(email)

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

def test_sign_in_nonexistent_user(client, mock_dynamodb):
    email = "nonexistent@example.com"
    code = "TESTCODE"

    # Mock the read_validation_code to return None (no code found)
    mock_dynamodb.read_validation_code.return_value = None

    # Test with nonexistent user
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