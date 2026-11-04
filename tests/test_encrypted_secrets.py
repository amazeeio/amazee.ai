import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError

from app.core.config import Settings, settings
from app.core.crypto import EncryptedString, fernet


@pytest.fixture
def column():
    return EncryptedString()


def test_settings_require_encryption_key(monkeypatch):
    monkeypatch.delenv("AMAZEEAI_ENCRYPTION_KEY")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_settings_reject_malformed_encryption_key(monkeypatch):
    monkeypatch.setenv("AMAZEEAI_ENCRYPTION_KEY", "CHANGE_ME")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)


def test_encrypted_string_round_trip(column):
    stored = column.process_bind_param("sk-secret", None)
    assert stored != "sk-secret"
    assert column.process_result_value(stored, None) == "sk-secret"


def test_encrypted_string_keeps_none_and_empty(column):
    for value in (None, ""):
        assert column.process_bind_param(value, None) == value
        assert column.process_result_value(value, None) == value


def test_encrypted_string_reads_plain_value(column):
    assert column.process_result_value("plain-value", None) == "plain-value"


def test_encrypted_string_decrypts_after_key_rotation(column, monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    old_token = Fernet(old).encrypt(b"rotated-secret").decode()

    monkeypatch.setattr(settings, "AMAZEEAI_ENCRYPTION_KEY", f"{new},{old}")
    fernet.cache_clear()
    try:
        assert column.process_result_value(old_token, None) == "rotated-secret"
        stored = column.process_bind_param("fresh-secret", None)
        assert Fernet(new).decrypt(stored.encode()) == b"fresh-secret"
    finally:
        # Later tests must use the conftest key again.
        fernet.cache_clear()
