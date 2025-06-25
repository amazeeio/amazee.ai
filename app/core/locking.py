from sqlalchemy.orm import Session
from sqlalchemy.exc import IntegrityError
from app.db.models import DBSystemSecret
from datetime import datetime, UTC, timedelta

def try_acquire_lock(lock_name: str, db: Session, lock_timeout: int = 10) -> bool:
    """
    Try to acquire a lock.
    If the lock is older than the timeout, a previous process stalled and the lock should be stolen.
    If the lock is not older than the timeout, the lock is still valid and the function returns False.
    If the lock does not exist, a new lock is created and the function returns True.

    Args:
        lock_name: The name of the lock.
        db: The database session.
        lock_timeout: The timeout in minutes for the lock.

    Returns:
        True if the lock is acquired, False otherwise.
    """
    lock_key = f"lock_{lock_name}"

    # First, try to get existing lock
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == lock_key).first()

    if lock:
        # Lock exists - check if it's active
        lock_active = lock.value.lower() == "true" and lock.updated_at > datetime.now(UTC) - timedelta(minutes=lock_timeout)
        if lock_active:
            return False
        else:
            # Lock exists but has been released or expired - steal it
            lock.value = "true"
            lock.updated_at = datetime.now(UTC)
            db.commit()
            return True
    else:
        # Lock doesn't exist - try to create it
        try:
            lock = DBSystemSecret(key=lock_key, value="true", updated_at=datetime.now(UTC))
            db.add(lock)
            db.commit()
            return True
        except IntegrityError:
            # Another process created the lock simultaneously
            db.rollback()
            return False

def release_lock(lock_name: str, db: Session):
    lock = db.query(DBSystemSecret).filter(DBSystemSecret.key == f"lock_{lock_name}").first()
    if lock:
        lock.value = "false"
        db.commit()
        return True
    else:
        return False
