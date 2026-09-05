"""Storage-state security module (Phase 9).

Per §5.6: Saved Playwright storage_state files hold active session cookies
and authentication tokens. They are treated as sensitive credentials, encrypted
at rest using Fernet (AES-128-CBC + HMAC-SHA256), and strictly excluded from git.
"""

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken

from core.config import settings

logger = logging.getLogger(__name__)

DEFAULT_KEY_FILE = Path("worker/storage/.secret.key")


def derive_fernet_key(passphrase_or_key: str | bytes) -> bytes:
    """Ensure key is a valid 32-byte urlsafe base64 Fernet key.
    If arbitrary passphrase is provided, hashes it to 32 bytes and base64 encodes it.
    """
    if isinstance(passphrase_or_key, str):
        passphrase_or_key = passphrase_or_key.strip().encode("utf-8")

    # If already a valid 44-character urlsafe base64 string
    try:
        decoded = base64.urlsafe_b64decode(passphrase_or_key)
        if len(decoded) == 32:
            return passphrase_or_key
    except Exception:
        pass

    # Deterministically derive 32-byte key via SHA-256
    digest = hashlib.sha256(passphrase_or_key).digest()
    return base64.urlsafe_b64encode(digest)


def get_encryption_key(key_override: str | bytes | None = None) -> bytes:
    """Resolve the encryption key from:
    1. Direct override argument
    2. WORKER_STORAGE_KEY environment variable / settings
    3. Persisted key file at worker/storage/.secret.key (auto-generated if missing)
    """
    if key_override is not None:
        return derive_fernet_key(key_override)

    if settings.WORKER_STORAGE_KEY:
        return derive_fernet_key(settings.WORKER_STORAGE_KEY)

    # Local fallback for local dev / testing
    key_file = DEFAULT_KEY_FILE
    if key_file.exists():
        return key_file.read_bytes().strip()

    key_file.parent.mkdir(parents=True, exist_ok=True)
    new_key = Fernet.generate_key()
    key_file.write_bytes(new_key)
    logger.info("Generated new worker storage encryption key at %s", key_file)
    return new_key


def encrypt_storage_state(
    data: dict[str, Any] | str,
    key: str | bytes | None = None,
) -> bytes:
    """Encrypt session state dictionary or JSON string into Fernet ciphertext bytes."""
    fernet = Fernet(get_encryption_key(key))
    if isinstance(data, dict):
        raw_json = json.dumps(data, indent=2)
    else:
        raw_json = str(data)

    return fernet.encrypt(raw_json.encode("utf-8"))


def decrypt_storage_state(
    encrypted_bytes: bytes,
    key: str | bytes | None = None,
) -> dict[str, Any]:
    """Decrypt Fernet ciphertext bytes back into session state dictionary."""
    fernet = Fernet(get_encryption_key(key))
    try:
        decrypted = fernet.decrypt(encrypted_bytes)
        return json.loads(decrypted.decode("utf-8"))
    except InvalidToken as exc:
        raise ValueError("Invalid storage state decryption key or corrupted ciphertext.") from exc


def save_encrypted_storage_state(
    data: dict[str, Any] | str,
    target_path: str | Path,
    key: str | bytes | None = None,
) -> Path:
    """Encrypt and save storage state to target path (typically .enc extension)."""
    p = Path(target_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    ciphertext = encrypt_storage_state(data, key=key)
    p.write_bytes(ciphertext)
    logger.info("Saved encrypted session state to %s (%d bytes)", p, len(ciphertext))
    return p


def load_decrypted_storage_state(
    source_path: str | Path,
    key: str | bytes | None = None,
) -> dict[str, Any]:
    """Read encrypted file and return decrypted session state dictionary."""
    p = Path(source_path)
    if not p.exists():
        raise FileNotFoundError(f"Encrypted session state file not found: {p}")
    ciphertext = p.read_bytes()
    return decrypt_storage_state(ciphertext, key=key)
