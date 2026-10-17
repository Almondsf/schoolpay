"""
Field-level encryption for sensitive personal data, and blind indexes for
looking those fields up.

`EncryptedString` encrypts with Fernet (AES-128-CBC + HMAC) under
FIELD_ENCRYPTION_KEYS. The first key encrypts; every key decrypts, so keys
rotate by prepending a new one and re-saving rows.

Ciphertexts are non-deterministic, so encrypted columns can't be filtered.
When a lookup is needed (e.g. a parent's phone at sign-in), store a
`blind_index()` of the normalised value alongside: an HMAC keyed separately
from the encryption keys, so it reveals nothing without the key.
"""

import base64
import hashlib
import hmac
from functools import lru_cache
from typing import Any

from cryptography.fernet import Fernet, InvalidToken, MultiFernet
from sqlalchemy import String, Text
from sqlalchemy.engine import Dialect
from sqlalchemy.types import TypeDecorator

from app.core.config import get_settings


class DecryptionError(RuntimeError):
    pass


@lru_cache(maxsize=4)
def _fernet(keys: tuple[str, ...]) -> MultiFernet:
    return MultiFernet([Fernet(key) for key in keys])


def fernet() -> MultiFernet:
    return _fernet(tuple(get_settings().field_encryption_keys))


def encrypt(value: str) -> str:
    return fernet().encrypt(value.encode()).decode()


def decrypt(token: str) -> str:
    try:
        return fernet().decrypt(token.encode()).decode()
    except InvalidToken as e:
        raise DecryptionError("Could not decrypt a field: wrong or missing FIELD_ENCRYPTION_KEYS.") from e


@lru_cache(maxsize=1)
def _index_key(secret_key: str) -> bytes:
    # Derived from SECRET_KEY with a fixed label so it's distinct from JWT signing.
    # Rotating SECRET_KEY means recomputing blind indexes (see docs/SECURITY.md).
    return hashlib.sha256(b"schoolpay.blind-index:" + secret_key.encode()).digest()


def blind_index(normalised_value: str) -> str:
    digest = hmac.new(_index_key(get_settings().secret_key), normalised_value.encode(), hashlib.sha256)
    return base64.urlsafe_b64encode(digest.digest()).decode().rstrip("=")


class EncryptedString(TypeDecorator[str]):
    """A string stored encrypted. Empty strings and NULL are stored as-is."""

    impl = Text
    cache_ok = True

    def process_bind_param(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None or value == "":
            return value
        return encrypt(value)

    def process_result_value(self, value: str | None, dialect: Dialect) -> str | None:
        if value is None or value == "":
            return value
        return decrypt(value)

    def coerce_compared_value(self, op: Any, value: Any) -> Any:
        raise TypeError("Encrypted columns can't be compared in SQL; use a blind index column.")


BLIND_INDEX_TYPE = String(43)  # urlsafe base64 of a SHA-256 HMAC, unpadded
