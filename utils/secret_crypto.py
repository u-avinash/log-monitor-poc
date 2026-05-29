"""Helpers for encrypting and decrypting project integration secrets."""

from __future__ import annotations

import base64
import hashlib
import os
from pathlib import Path

from cryptography.fernet import Fernet, InvalidToken

from config.settings import get_settings

settings = get_settings()


def _get_key_material() -> bytes:
    configured = (settings.integration_secret_key or "").strip()
    if configured:
        return configured.encode("utf-8")

    key_file = Path(settings.integration_secret_key_file)
    key_file.parent.mkdir(parents=True, exist_ok=True)

    if key_file.exists():
        return key_file.read_text(encoding="utf-8").strip().encode("utf-8")

    generated = base64.urlsafe_b64encode(os.urandom(32)).decode("utf-8")
    key_file.write_text(generated, encoding="utf-8")
    try:
        os.chmod(key_file, 0o600)
    except Exception:
        pass
    return generated.encode("utf-8")


def _build_fernet() -> Fernet:
    key_material = _get_key_material()
    digest = hashlib.sha256(key_material).digest()
    return Fernet(base64.urlsafe_b64encode(digest))


def encrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    return _build_fernet().encrypt(value.encode("utf-8")).decode("utf-8")


def decrypt_secret(value: str | None) -> str | None:
    if value is None:
        return None
    if value == "":
        return ""
    try:
        return _build_fernet().decrypt(value.encode("utf-8")).decode("utf-8")
    except (InvalidToken, ValueError):
        return value
