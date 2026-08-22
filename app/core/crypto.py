"""Envelope encryption for secrets at rest, with key versioning + rotation.

ENCRYPTION_KEY holds one OR MORE comma-separated urlsafe-base64 Fernet keys; the
FIRST is the active (primary) encryption key and the rest are retired keys kept
only for decryption. `MultiFernet` encrypts with the primary and decrypts with
any, so you rotate by prepending a new key and re-encrypting lazily via
`rotate_token` — no manual re-encryption and zero downtime. Outside production an
unset key is derived from JWT_SECRET; production refuses to boot without a valid
key (enforced by Settings.production_config_errors).
"""
from __future__ import annotations

import base64
import hashlib
from typing import Optional

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

from app.core.config import Settings, get_settings


class EncryptionError(RuntimeError):
    """Raised when ciphertext cannot be decrypted (tampered / unknown key)."""


_CIPHER: Optional[MultiFernet] = None


def _derive_dev_key(settings: Settings) -> bytes:
    """Deterministic, dev-only Fernet key derived from JWT_SECRET (never for prod)."""
    digest = hashlib.sha256(settings.jwt_secret.encode("utf-8")).digest()
    return base64.urlsafe_b64encode(digest)


def parse_keys(settings: Settings) -> list[bytes]:
    """Ordered key material: primary first, then retired keys (for decryption)."""
    raw = settings.encryption_key.strip()
    if raw:
        return [k.strip().encode("utf-8") for k in raw.split(",") if k.strip()]
    if settings.is_production:  # defensive: preflight should have blocked boot already
        raise EncryptionError("ENCRYPTION_KEY is required in production.")
    return [_derive_dev_key(settings)]


def build_cipher(keys: list[bytes]) -> MultiFernet:
    return MultiFernet([Fernet(k) for k in keys])


def get_cipher() -> MultiFernet:
    global _CIPHER
    if _CIPHER is None:
        _CIPHER = build_cipher(parse_keys(get_settings()))
    return _CIPHER


def _install_cipher(cipher: MultiFernet) -> None:
    """Swap the active cipher (used by rotation flows and tests)."""
    global _CIPHER
    _CIPHER = cipher


def reset_cipher() -> None:
    global _CIPHER
    _CIPHER = None


def encrypt(plaintext: str) -> str:
    """Encrypt with the PRIMARY key, returning urlsafe-base64 ciphertext."""
    return get_cipher().encrypt(plaintext.encode("utf-8")).decode("utf-8")


def decrypt(token: str) -> str:
    """Decrypt with ANY configured key; raises EncryptionError on tamper/unknown."""
    try:
        return get_cipher().decrypt(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError("Failed to decrypt sensitive value.") from exc


def rotate_token(token: str) -> str:
    """Re-encrypt existing ciphertext under the PRIMARY key (envelope rotation)."""
    try:
        return get_cipher().rotate(token.encode("utf-8")).decode("utf-8")
    except InvalidToken as exc:
        raise EncryptionError("Failed to rotate sensitive value.") from exc
