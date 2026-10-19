"""Encrypting the GitHub tokens we keep for private repos.

Fernet (from the `cryptography` library) is authenticated encryption: a token can only be read
with the key, and any tampering with the stored value is detected. The key is derived from the
TOKEN_ENCRYPTION_KEY setting, so any long random string works there.
"""

import base64
import hashlib

from cryptography.fernet import Fernet, InvalidToken

from app.config import Settings


class TokenDecryptError(Exception):
    """A stored token can't be read: the key changed, or the value was altered."""


class TokenCipher:
    def __init__(self, secret: str) -> None:
        if not secret:
            raise ValueError("TokenCipher needs a non-empty secret.")
        # Fernet wants exactly 32 bytes, base64-encoded; SHA-256 of the secret gives that.
        key = base64.urlsafe_b64encode(hashlib.sha256(secret.encode()).digest())
        self._fernet = Fernet(key)

    def encrypt(self, value: str) -> str:
        return self._fernet.encrypt(value.encode()).decode()

    def decrypt(self, value: str) -> str:
        try:
            return self._fernet.decrypt(value.encode()).decode()
        except InvalidToken as exc:
            raise TokenDecryptError("A stored GitHub token couldn't be decrypted.") from exc


def create_token_cipher(settings: Settings) -> TokenCipher | None:
    # None when private repos aren't set up.
    if not settings.private_repos_enabled or settings.token_encryption_key is None:
        return None
    return TokenCipher(settings.token_encryption_key.get_secret_value())
