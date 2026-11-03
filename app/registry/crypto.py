"""Encryption of credential values at rest, with keys from the environment."""

import json
import os

from cryptography.fernet import Fernet, MultiFernet

from app.registry.config import CREDENTIAL_KEYS_ENV


class MissingKey(Exception):
    pass


CANNOT_DECRYPT = f"the credential values cannot be decrypted with the keys in {CREDENTIAL_KEYS_ENV}"


def _keys() -> list[Fernet]:
    raw = [k.strip() for k in os.getenv(CREDENTIAL_KEYS_ENV, "").split(",") if k.strip()]
    if not raw:
        raise MissingKey(f"{CREDENTIAL_KEYS_ENV} is not set, so credential values cannot be stored")
    try:
        return [Fernet(k) for k in raw]
    except (ValueError, TypeError):
        # The exception text can hold the key, so it is not passed on.
        raise MissingKey(f"{CREDENTIAL_KEYS_ENV} holds a key that is not a valid Fernet key") from None


def encrypt_values(values: dict) -> str:
    return _keys()[0].encrypt(json.dumps(values).encode()).decode()


def decrypt_values(token: str) -> dict:
    return json.loads(MultiFernet(_keys()).decrypt(token.encode()))
