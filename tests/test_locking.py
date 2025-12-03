from datetime import datetime, UTC, timedelta
from app.core.locking import try_acquire_lock, release_lock
from app.db.models import DBSystemSecret


def test_try_acquire_lock_new_lock(db):
    """
    Test acquiring a lock that doesn't exist.

    GIVEN: No lock exists for the given name
    WHEN: try_acquire_lock is called
    THEN: A new lock should be created and True should be returned
    """
    # Act
    result = try_acquire_lock("test_lock", db)

    # Assert
    assert result is True

    # Verify lock was created in database
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert lock is not None
    assert lock.value == "true"
    assert lock.key == "lock_test_lock"


def test_try_acquire_lock_existing_valid_lock(db):
    """
    Test attempting to acquire a lock that exists and is still valid.

    GIVEN: A lock exists and is not older than the timeout
    WHEN: try_acquire_lock is called
    THEN: False should be returned and the existing lock should remain unchanged
    """
    # Arrange - Create a lock that's 5 minutes old (within 10-minute timeout)
    existing_lock = DBSystemSecret(
        key="lock_test_lock",
        value="true",
        updated_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    db.add(existing_lock)
    db.commit()

    # Act
    result = try_acquire_lock("test_lock", db)

    # Assert
    assert result is False

    # Verify the original lock is still there and unchanged
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert lock is not None
    assert lock.value == "true"
    # The updated_at should still be the original time (not changed)
    assert lock.updated_at == existing_lock.updated_at


def test_try_acquire_lock_expired_lock(db):
    """
    Test acquiring a lock that exists but has expired.

    GIVEN: A lock exists but is older than the timeout
    WHEN: try_acquire_lock is called
    THEN: The lock should be stolen (replaced) and True should be returned
    """
    # Arrange - Create a lock that's 15 minutes old (beyond 10-minute timeout)
    old_lock = DBSystemSecret(
        key="lock_test_lock",
        value="true",
        updated_at=datetime.now(UTC) - timedelta(minutes=15)
    )
    db.add(old_lock)
    db.commit()

    # Act
    result = try_acquire_lock("test_lock", db)

    # Assert
    assert result is True

    # Verify the lock was updated (stolen)
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert lock is not None
    assert lock.value == "true"
    # The updated_at should be recent (indicating the lock was stolen)
    assert lock.updated_at > datetime.now(UTC) - timedelta(minutes=1)


def test_try_acquire_lock_custom_timeout(db):
    """
    Test acquiring a lock with a custom timeout value.

    GIVEN: A lock exists that's 5 minutes old with a 3-minute timeout
    WHEN: try_acquire_lock is called with lock_timeout=3
    THEN: The lock should be stolen and True should be returned
    """
    # Arrange - Create a lock that's 5 minutes old
    old_lock = DBSystemSecret(
        key="lock_test_lock",
        value="true",
        updated_at=datetime.now(UTC) - timedelta(minutes=5)
    )
    db.add(old_lock)
    db.commit()

    # Act - Use 3-minute timeout (lock is older than this)
    result = try_acquire_lock("test_lock", db, lock_timeout=3)

    # Assert
    assert result is True

    # Verify the lock was stolen
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert lock is not None
    assert lock.updated_at > datetime.now(UTC) - timedelta(minutes=1)


def test_try_acquire_lock_zero_timeout(db):
    """
    Test acquiring a lock with zero timeout (immediate expiration).

    GIVEN: A lock exists with zero timeout
    WHEN: try_acquire_lock is called with lock_timeout=0
    THEN: Any existing lock should be stolen and True should be returned
    """
    # Arrange - Create a lock that's 1 minute old
    old_lock = DBSystemSecret(
        key="lock_test_lock",
        value="true",
        updated_at=datetime.now(UTC) - timedelta(minutes=1)
    )
    db.add(old_lock)
    db.commit()

    # Act - Use 0-minute timeout
    result = try_acquire_lock("test_lock", db, lock_timeout=0)

    # Assert
    assert result is True

    # Verify the lock was stolen
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert lock is not None
    assert lock.updated_at > datetime.now(UTC) - timedelta(minutes=1)


def test_try_acquire_lock_multiple_locks(db):
    """
    Test acquiring multiple different locks.

    GIVEN: Multiple locks with different names
    WHEN: try_acquire_lock is called for each lock
    THEN: Each lock should be created independently
    """
    # Act
    result1 = try_acquire_lock("lock1", db)
    result2 = try_acquire_lock("lock2", db)
    result3 = try_acquire_lock("lock3", db)

    # Assert
    assert result1 is True
    assert result2 is True
    assert result3 is True

    # Verify all locks were created
    lock1 = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_lock1").first()
    lock2 = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_lock2").first()
    lock3 = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_lock3").first()

    assert lock1 is not None
    assert lock2 is not None
    assert lock3 is not None
    assert lock1.value == "true"
    assert lock2.value == "true"
    assert lock3.value == "true"


def test_release_lock_existing_lock(db):
    """
    Test releasing an existing lock.

    GIVEN: A lock exists in the database
    WHEN: release_lock is called
    THEN: The lock value should be set to "false" and True should be returned
    """
    # Arrange - Create a lock
    lock = DBSystemSecret(key="lock_test_lock", value="true")
    db.add(lock)
    db.commit()

    # Act
    result = release_lock("test_lock", db)

    # Assert
    assert result is True

    # Verify the lock was updated
    updated_lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert updated_lock is not None
    assert updated_lock.value == "false"


def test_release_lock_nonexistent_lock(db):
    """
    Test releasing a lock that doesn't exist.

    GIVEN: No lock exists for the given name
    WHEN: release_lock is called
    THEN: False should be returned and no lock should be created
    """
    # Act
    result = release_lock("nonexistent_lock", db)

    # Assert
    assert result is False

    # Verify no lock was created
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_nonexistent_lock").first()
    assert lock is None


def test_release_lock_already_released(db):
    """
    Test releasing a lock that's already been released.

    GIVEN: A lock exists with value "false"
    WHEN: release_lock is called
    THEN: The lock should remain "false" and True should be returned
    """
    # Arrange - Create a lock that's already released
    lock = DBSystemSecret(key="lock_test_lock", value="false")
    db.add(lock)
    db.commit()

    # Act
    result = release_lock("test_lock", db)

    # Assert
    assert result is True

    # Verify the lock remains "false"
    updated_lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_test_lock").first()
    assert updated_lock is not None
    assert updated_lock.value == "false"


def test_lock_lifecycle(db):
    """
    Test the complete lifecycle of a lock: acquire, try to acquire again, release, acquire again.

    GIVEN: No lock exists initially
    WHEN: The lock lifecycle is executed
    THEN: Each step should work as expected
    """
    # Step 1: Acquire lock
    result1 = try_acquire_lock("lifecycle_lock", db)
    assert result1 is True

    # Step 2: Try to acquire the same lock again (should fail)
    result2 = try_acquire_lock("lifecycle_lock", db)
    assert result2 is False

    # Step 3: Release the lock
    result3 = release_lock("lifecycle_lock", db)
    assert result3 is True

    # Step 4: Try to acquire the lock again (should succeed)
    result4 = try_acquire_lock("lifecycle_lock", db)
    assert result4 is True

    # Verify the final state
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_lifecycle_lock").first()
    assert lock is not None
    assert lock.value == "true"


def test_lock_with_special_characters(db):
    """
    Test lock names with special characters.

    GIVEN: A lock name with special characters
    WHEN: try_acquire_lock and release_lock are called
    THEN: The lock should work correctly
    """
    lock_name = "test-lock_with_underscores.and.dots"

    # Act
    result1 = try_acquire_lock(lock_name, db)
    result2 = release_lock(lock_name, db)

    # Assert
    assert result1 is True
    assert result2 is True

    # Verify the lock was created and released
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == f"lock_{lock_name}").first()
    assert lock is not None
    assert lock.value == "false"


def test_lock_with_unicode_characters(db):
    """
    Test lock names with unicode characters.

    GIVEN: A lock name with unicode characters
    WHEN: try_acquire_lock and release_lock are called
    THEN: The lock should work correctly
    """
    lock_name = "test_lock_🚀_with_emoji"

    # Act
    result1 = try_acquire_lock(lock_name, db)
    result2 = release_lock(lock_name, db)

    # Assert
    assert result1 is True
    assert result2 is True

    # Verify the lock was created and released
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == f"lock_{lock_name}").first()
    assert lock is not None
    assert lock.value == "false"


def test_concurrent_lock_attempts(db):
    """
    Test multiple attempts to acquire the same lock concurrently.

    GIVEN: A lock is acquired
    WHEN: Multiple attempts are made to acquire the same lock
    THEN: Only the first should succeed, others should fail
    """
    # First acquisition should succeed
    result1 = try_acquire_lock("concurrent_lock", db)
    assert result1 is True

    # Subsequent acquisitions should fail
    result2 = try_acquire_lock("concurrent_lock", db)
    result3 = try_acquire_lock("concurrent_lock", db)
    result4 = try_acquire_lock("concurrent_lock", db)

    assert result2 is False
    assert result3 is False
    assert result4 is False

    # Verify only one lock exists
    locks = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_concurrent_lock").all()
    assert len(locks) == 1
    assert locks[0].value == "true"


def test_lock_timeout_edge_case(db):
    """
    Test lock timeout at the exact boundary.

    GIVEN: A lock that's exactly at the timeout boundary
    WHEN: try_acquire_lock is called
    THEN: The behavior should be consistent
    """
    # Arrange - Create a lock that's exactly 10 minutes old (default timeout)
    exact_time = datetime.now(UTC) - timedelta(minutes=10)
    old_lock = DBSystemSecret(
        key="lock_edge_lock",
        value="true",
        updated_at=exact_time
    )
    db.add(old_lock)
    db.commit()

    # Act
    result = try_acquire_lock("edge_lock", db)

    # Assert - Should steal the lock since it's exactly at the timeout
    assert result is True

    # Verify the lock was updated
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_edge_lock").first()
    assert lock is not None
    assert lock.updated_at > exact_time


def test_lock_with_empty_name(db):
    """
    Test lock with empty name.

    GIVEN: An empty lock name
    WHEN: try_acquire_lock and release_lock are called
    THEN: The lock should work correctly
    """
    # Act
    result1 = try_acquire_lock("", db)
    result2 = release_lock("", db)

    # Assert
    assert result1 is True
    assert result2 is True

    # Verify the lock was created and released
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == "lock_").first()
    assert lock is not None
    assert lock.value == "false"


def test_lock_with_very_long_name(db):
    """
    Test lock with a very long name.

    GIVEN: A very long lock name
    WHEN: try_acquire_lock and release_lock are called
    THEN: The lock should work correctly
    """
    long_name = "a" * 1000  # 1000 character name

    # Act
    result1 = try_acquire_lock(long_name, db)
    result2 = release_lock(long_name, db)

    # Assert
    assert result1 is True
    assert result2 is True

    # Verify the lock was created and released
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == f"lock_{long_name}").first()
    assert lock is not None
    assert lock.value == "false"