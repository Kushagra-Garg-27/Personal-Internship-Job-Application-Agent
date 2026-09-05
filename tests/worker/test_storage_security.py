"""Tests for storage-state encryption and security (Phase 9)."""

from __future__ import annotations

import pytest
from pathlib import Path
from worker.security.storage import (
    derive_fernet_key,
    encrypt_storage_state,
    decrypt_storage_state,
    save_encrypted_storage_state,
    load_decrypted_storage_state,
)


def test_derive_fernet_key():
    k1 = derive_fernet_key("my-custom-passphrase")
    k2 = derive_fernet_key("my-custom-passphrase")
    assert k1 == k2
    assert len(k1) == 44  # Base64 encoded 32 bytes with padding


def test_encrypt_and_decrypt_dict():
    sample_state = {
        "cookies": [{"name": "session_id", "value": "abc123secret", "domain": ".example.com"}],
        "origins": [],
    }
    key = derive_fernet_key("test-key")
    encrypted = encrypt_storage_state(sample_state, key=key)
    assert isinstance(encrypted, bytes)
    assert b"abc123secret" not in encrypted  # Ciphertext must not expose tokens

    decrypted = decrypt_storage_state(encrypted, key=key)
    assert decrypted == sample_state


def test_invalid_key_raises_error():
    sample_state = {"token": "secret"}
    encrypted = encrypt_storage_state(sample_state, key="key-1")

    with pytest.raises(ValueError, match="Invalid storage state decryption key"):
        decrypt_storage_state(encrypted, key="wrong-key")


def test_save_and_load_encrypted_file(tmp_path: Path):
    sample_state = {
        "cookies": [{"name": "session", "value": "xyz789"}],
    }
    file_path = tmp_path / "test_session.enc"
    key = derive_fernet_key("file-test-key")

    saved_p = save_encrypted_storage_state(sample_state, file_path, key=key)
    assert saved_p.exists()
    assert b"xyz789" not in saved_p.read_bytes()

    loaded_state = load_decrypted_storage_state(file_path, key=key)
    assert loaded_state == sample_state
