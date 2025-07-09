import pytest
from datetime import datetime, UTC, timedelta
from app.db.models import DBAuditLog, DBUser
from app.core.security import get_password_hash


def test_get_audit_logs_admin_access_success(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs
    Then: The request succeeds and returns audit logs
    """
    # Create some test audit logs
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    # Create test audit logs
    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            resource_id="123",
            action="GET /users/123",
            details={"path": "/users/123", "query_params": {}, "status_code": 200},
            ip_address="127.0.0.1",
            user_agent="test-agent",
            request_source="frontend",
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="auth",
            resource_id=None,
            action="POST /auth/login",
            details={"path": "/auth/login", "query_params": {}, "status_code": 200},
            ip_address="127.0.0.1",
            user_agent="test-agent",
            request_source="api",
            timestamp=datetime.now(UTC) - timedelta(hours=1)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    response = client.get(
        "/audit/logs",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "items" in data
    assert "total" in data
    assert data["total"] >= 2
    assert len(data["items"]) >= 2


def test_get_audit_logs_non_admin_forbidden(client, test_token):
    """
    Given: A non-admin user with valid authentication
    When: They request audit logs
    Then: The request is forbidden
    """
    response = client.get(
        "/audit/logs",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to access audit logs" in response.json()["detail"]


def test_get_audit_logs_no_auth_forbidden(client):
    """
    Given: A request without authentication
    When: They request audit logs
    Then: The request is forbidden
    """
    response = client.get("/audit/logs")

    assert response.status_code == 401


def test_get_audit_logs_with_event_type_filter(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs filtered by event type
    Then: Only logs with matching event types are returned
    """
    # Create test audit logs with different event types
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="auth",
            action="POST /auth/login",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="DELETE",
            resource_type="users",
            action="DELETE /users/123",
            details={"status_code": 204},
            timestamp=datetime.now(UTC)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    response = client.get(
        "/audit/logs?event_type=GET,POST",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2

    # Check that only GET and POST events are returned
    event_types = [log["event_type"] for log in data["items"]]
    assert all(et in ["GET", "POST"] for et in event_types)


def test_get_audit_logs_with_resource_type_filter(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs filtered by resource type
    Then: Only logs with matching resource types are returned
    """
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="auth",
            action="POST /auth/login",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="teams",
            action="GET /teams",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    response = client.get(
        "/audit/logs?resource_type=users,auth",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2

    # Check that only users and auth resource types are returned
    resource_types = [log["resource_type"] for log in data["items"]]
    assert all(rt in ["users", "auth"] for rt in resource_types)


def test_get_audit_logs_with_user_email_filter(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs filtered by user email
    Then: Only logs from users with matching email are returned
    """
    # Create users with different emails
    user1 = DBUser(
        email="user1@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_admin=False
    )
    user2 = DBUser(
        email="user2@example.com",
        hashed_password=get_password_hash("password"),
        is_active=True,
        is_admin=False
    )
    db.add_all([user1, user2])
    db.commit()
    db.refresh(user1)
    db.refresh(user2)

    audit_logs = [
        DBAuditLog(
            user_id=user1.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=user2.id,
            event_type="POST",
            resource_type="auth",
            action="POST /auth/login",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    response = client.get(
        "/audit/logs?user_email=user1",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    # Check that only logs from user1 are returned
    user_emails = [log["user_email"] for log in data["items"] if log["user_email"]]
    assert all(email == "user1@example.com" for email in user_emails)


def test_get_audit_logs_with_date_range_filter(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs filtered by date range
    Then: Only logs within the specified date range are returned
    """
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    now = datetime.now(UTC)
    yesterday = now - timedelta(days=1)
    tomorrow = now + timedelta(days=1)

    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=yesterday
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="auth",
            action="POST /auth/login",
            details={"status_code": 200},
            timestamp=now
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="DELETE",
            resource_type="users",
            action="DELETE /users/123",
            details={"status_code": 204},
            timestamp=tomorrow
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    # Filter for logs between yesterday and tomorrow
    # Use ISO format with 'Z' suffix for UTC
    from_date = yesterday.strftime("%Y-%m-%dT%H:%M:%SZ")
    to_date = tomorrow.strftime("%Y-%m-%dT%H:%M:%SZ")

    response = client.get(
        f"/audit/logs?from_date={from_date}&to_date={to_date}",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 3


def test_get_audit_logs_with_status_code_filter(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs filtered by status code
    Then: Only logs with matching status codes are returned
    """
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="auth",
            action="POST /auth/login",
            details={"status_code": 401},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="DELETE",
            resource_type="users",
            action="DELETE /users/123",
            details={"status_code": 404},
            timestamp=datetime.now(UTC)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    response = client.get(
        "/audit/logs?status_code=200,401",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 2

    # Check that only logs with status codes 200 or 401 are returned
    # The status codes in the response are integers, not strings
    status_codes = [log["details"]["status_code"] for log in data["items"] if log["details"]]
    assert all(sc in [200, 401] for sc in status_codes)


def test_get_audit_logs_with_pagination(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs with pagination parameters
    Then: The correct number of logs are returned with proper pagination
    """
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    # Create 5 audit logs
    audit_logs = []
    for i in range(5):
        log = DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action=f"GET /users/{i}",
            details={"status_code": 200},
            timestamp=datetime.now(UTC) - timedelta(hours=i)
        )
        audit_logs.append(log)

    for log in audit_logs:
        db.add(log)
    db.commit()

    # Test pagination with limit=2, skip=1
    response = client.get(
        "/audit/logs?limit=2&skip=1",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 5
    assert len(data["items"]) == 2


def test_get_audit_logs_invalid_pagination_parameters(client, admin_token):
    """
    Given: An admin user with valid authentication
    When: They request audit logs with invalid pagination parameters
    Then: The request fails with appropriate error
    """
    # Test negative skip
    response = client.get(
        "/audit/logs?skip=-1",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422

    # Test invalid limit
    response = client.get(
        "/audit/logs?limit=0",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422

    # Test limit too high
    response = client.get(
        "/audit/logs?limit=1001",
        headers={"Authorization": f"Bearer {admin_token}"}
    )
    assert response.status_code == 422


def test_get_audit_logs_metadata_admin_access_success(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs metadata
    Then: The request succeeds and returns metadata
    """
    # Create some test audit logs with different types
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="auth",
            action="POST /auth/login",
            details={"status_code": 401},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="DELETE",
            resource_type="teams",
            action="DELETE /teams/123",
            details={"status_code": 404},
            timestamp=datetime.now(UTC)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    response = client.get(
        "/audit/logs/metadata",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert "event_types" in data
    assert "resource_types" in data
    assert "status_codes" in data

    # Check that the metadata contains the expected values
    assert "GET" in data["event_types"]
    assert "POST" in data["event_types"]
    assert "DELETE" in data["event_types"]
    assert "users" in data["resource_types"]
    assert "auth" in data["resource_types"]
    assert "teams" in data["resource_types"]
    # Status codes are returned as strings from the metadata endpoint
    assert "200" in data["status_codes"]
    assert "401" in data["status_codes"]
    assert "404" in data["status_codes"]


def test_get_audit_logs_metadata_non_admin_forbidden(client, test_token):
    """
    Given: A non-admin user with valid authentication
    When: They request audit logs metadata
    Then: The request is forbidden
    """
    response = client.get(
        "/audit/logs/metadata",
        headers={"Authorization": f"Bearer {test_token}"}
    )

    assert response.status_code == 403
    assert "Not authorized to access audit logs metadata" in response.json()["detail"]


def test_get_audit_logs_metadata_no_auth_forbidden(client):
    """
    Given: A request without authentication
    When: They request audit logs metadata
    Then: The request is forbidden
    """
    response = client.get("/audit/logs/metadata")

    assert response.status_code == 401


def test_get_audit_logs_with_combined_filters(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs with multiple filters
    Then: Only logs matching all filters are returned
    """
    test_user = db.query(DBUser).filter(DBUser.email == "test@example.com").first()
    if not test_user:
        test_user = DBUser(
            email="test@example.com",
            hashed_password=get_password_hash("testpassword"),
            is_active=True,
            is_admin=False
        )
        db.add(test_user)
        db.commit()
        db.refresh(test_user)

    audit_logs = [
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="users",
            action="GET /users",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="POST",
            resource_type="users",
            action="POST /users",
            details={"status_code": 201},
            timestamp=datetime.now(UTC)
        ),
        DBAuditLog(
            user_id=test_user.id,
            event_type="GET",
            resource_type="auth",
            action="GET /auth/me",
            details={"status_code": 200},
            timestamp=datetime.now(UTC)
        )
    ]

    for log in audit_logs:
        db.add(log)
    db.commit()

    # Filter by event_type=GET and resource_type=users
    response = client.get(
        "/audit/logs?event_type=GET&resource_type=users",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    # Check that only GET events on users resource are returned
    for log in data["items"]:
        assert log["event_type"] == "GET"
        assert log["resource_type"] == "users"


def test_get_audit_logs_empty_result(client, admin_token):
    """
    Given: An admin user with valid authentication
    When: They request audit logs with filters that match no records
    Then: An empty result set is returned
    """
    response = client.get(
        "/audit/logs?event_type=NONEXISTENT",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 0
    assert len(data["items"]) == 0


def test_get_audit_logs_with_null_user(client, admin_token, db):
    """
    Given: An admin user with valid authentication
    When: They request audit logs that include entries with null user_id
    Then: The logs are returned correctly with null user information
    """
    # Create audit log without user_id
    audit_log = DBAuditLog(
        user_id=None,
        event_type="GET",
        resource_type="public",
        action="GET /public/health",
        details={"status_code": 200},
        timestamp=datetime.now(UTC)
    )
    db.add(audit_log)
    db.commit()

    response = client.get(
        "/audit/logs",
        headers={"Authorization": f"Bearer {admin_token}"}
    )

    assert response.status_code == 200
    data = response.json()
    assert data["total"] >= 1

    # Find the log with null user
    null_user_log = next((log for log in data["items"] if log["user_id"] is None), None)
    assert null_user_log is not None
    assert null_user_log["user_email"] is None