from functools import cache

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String
from sqlalchemy.types import TypeDecorator

from app.core.config import encryption_keys, settings


@cache
def fernet() -> MultiFernet:
    return MultiFernet([Fernet(key) for key in encryption_keys(settings.AMAZEEAI_ENCRYPTION_KEY)])


class EncryptedString(TypeDecorator):
    """A string column stored as a Fernet token."""

    impl = String
    cache_ok = True

    def process_bind_param(self, value, dialect):
        if not value:
            return value
        return fernet().encrypt(value.encode()).decode()

    def process_result_value(self, value, dialect):
        if not value:
            return value
        try:
            return fernet().decrypt(value.encode()).decode()
        except InvalidToken:
            # Rows written before encryption are still plain text; a later release removes this fallback.
            return value
