import pytest
from cryptography.fernet import Fernet
from pydantic import ValidationError
from sqlalchemy import text

from app.core.config import Settings, settings
from app.core.crypto import EncryptedString, fernet
from app.db.models import DBPrivateAIKey, DBRegion


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


def _seed_key(db, region):
    key = DBPrivateAIKey(
        name="secret-key",
        litellm_token="sk-plain-token",
        litellm_api_url="http://test-llm",
        database_name="db-secret",
        database_host="host",
        database_username="u",
        database_password="db-plain-password",
        region_id=region.id,
    )
    db.add(key)
    db.commit()
    return key


def _raw(db, sql, **params):
    return db.execute(text(sql), params).one()


def test_secret_columns_are_encrypted_at_rest(db, test_region):
    key = _seed_key(db, test_region)
    plain = {
        "litellm_api_key": "test-litellm-key",
        "postgres_admin_password": "postgres",
        "litellm_token": "sk-plain-token",
        "database_password": "db-plain-password",
    }

    raw = dict(
        zip(
            ("litellm_api_key", "postgres_admin_password"),
            _raw(db, "SELECT litellm_api_key, postgres_admin_password FROM regions WHERE id = :id", id=test_region.id),
        )
    )
    raw.update(
        zip(
            ("litellm_token", "database_password"),
            _raw(db, "SELECT litellm_token, database_password FROM ai_tokens WHERE id = :id", id=key.id),
        )
    )
    for column, value in plain.items():
        assert raw[column] != value
        assert fernet().decrypt(raw[column].encode()).decode() == value

    db.expire_all()
    assert test_region.litellm_api_key == "test-litellm-key"
    assert test_region.postgres_admin_password == "postgres"
    assert key.litellm_token == "sk-plain-token"
    assert key.database_password == "db-plain-password"


def test_secret_columns_read_plain_rows(db, test_region):
    key = _seed_key(db, test_region)
    db.execute(
        text("UPDATE regions SET litellm_api_key = 'plain-api-key', postgres_admin_password = 'plain-pg' WHERE id = :id"),
        {"id": test_region.id},
    )
    db.execute(
        text("UPDATE ai_tokens SET litellm_token = 'plain-token', database_password = 'plain-db' WHERE id = :id"),
        {"id": key.id},
    )
    db.commit()
    db.expire_all()

    assert test_region.litellm_api_key == "plain-api-key"
    assert test_region.postgres_admin_password == "plain-pg"
    assert key.litellm_token == "plain-token"
    assert key.database_password == "plain-db"


def test_empty_region_key_stays_empty(db):
    region = DBRegion(name="empty-key-region", litellm_api_url="http://llm", litellm_api_key="", is_active=True)
    db.add(region)
    db.commit()

    assert _raw(db, "SELECT litellm_api_key FROM regions WHERE id = :id", id=region.id)[0] == ""
    matches = db.query(DBRegion.id).filter(DBRegion.litellm_api_key != "").all()
    assert (region.id,) not in matches


def test_private_ai_keys_response_is_plain(client, admin_token, db, test_region):
    key = _seed_key(db, test_region)

    response = client.get(
        "/private-ai-keys?show_all=true",
        headers={"Authorization": f"Bearer {admin_token}"},
    )

    assert response.status_code == 200
    (returned,) = [k for k in response.json() if k["id"] == key.id]
    assert returned["litellm_token"] == "sk-plain-token"
    assert returned["database_password"] == "db-plain-password"
