"""Security helpers for browser auth, password hashing, and secret storage."""

from __future__ import annotations

import base64
import hmac
import hashlib
import secrets

from argon2 import PasswordHasher
from argon2.low_level import Type
from cryptography.fernet import Fernet

from .config import Settings


_password_hasher = PasswordHasher(type=Type.ID, time_cost=3, memory_cost=65536, parallelism=4)


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def new_browser_token() -> str:
    return secrets.token_urlsafe(32)


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def csrf_token_for_session(session_token: str, settings: Settings) -> str:
    digest = hmac.new(settings.app_secret.encode("utf-8"), session_token.encode("utf-8"), hashlib.sha256).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def verify_csrf_token(session_token: str | None, csrf_token: str | None, settings: Settings) -> bool:
    if not session_token or not csrf_token:
        return False
    expected = csrf_token_for_session(session_token, settings)
    return secrets.compare_digest(expected, csrf_token)


def api_key_hint(value: str) -> str:
    value = value.strip()
    if len(value) <= 8:
        return value
    return f"{value[:4]}...{value[-4:]}"


class SecretCipher:
    def __init__(self, settings: Settings) -> None:
        self._fernet = Fernet(settings.effective_encryption_key.encode("utf-8"))

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode("utf-8")).decode("utf-8")

    def decrypt(self, value: str) -> str:
        return self._fernet.decrypt(value.encode("utf-8")).decode("utf-8")
