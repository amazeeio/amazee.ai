import pytest
from cryptography.fernet import Fernet

from app.registry.config import CREDENTIAL_KEYS_ENV
from app.registry.crypto import MissingKey, decrypt_values, encrypt_values


def test_values_survive_a_round_trip(monkeypatch):
    monkeypatch.setenv(CREDENTIAL_KEYS_ENV, Fernet.generate_key().decode())
    values = {"aws_access_key_id": "AKIA-plain-value", "aws_region_name": "us-east-1"}
    token = encrypt_values(values)
    assert "AKIA-plain-value" not in token
    assert decrypt_values(token) == values


def test_an_old_key_still_decrypts_after_a_new_key_is_put_first(monkeypatch):
    old, new = Fernet.generate_key().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(CREDENTIAL_KEYS_ENV, old)
    token = encrypt_values({"api_key": "sk-old"})
    monkeypatch.setenv(CREDENTIAL_KEYS_ENV, f"{new},{old}")
    assert decrypt_values(token) == {"api_key": "sk-old"}


@pytest.mark.parametrize("value", [None, "", " , ", "not-a-fernet-key"])
def test_a_missing_or_bad_key_raises_missing_key(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(CREDENTIAL_KEYS_ENV, raising=False)
    else:
        monkeypatch.setenv(CREDENTIAL_KEYS_ENV, value)
    with pytest.raises(MissingKey, match=CREDENTIAL_KEYS_ENV) as exc:
        encrypt_values({"api_key": "sk-x"})
    assert "not-a-fernet-key" not in str(exc.value)
