"""Ephemeral RSA keypair for local dev/test tokens.

Generated in-process at import time. Nothing is persisted, nothing is committed,
and this module is never used when auth_dev_mode is False.
"""

from __future__ import annotations

from functools import lru_cache
from typing import Any

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa


@lru_cache(maxsize=1)
def _keypair() -> tuple[Any, Any]:
    private = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    return private, private.public_key()


def dev_private_key() -> Any:
    return _keypair()[0]


def dev_public_key() -> Any:
    return _keypair()[1]


def dev_private_pem() -> bytes:
    return dev_private_key().private_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PrivateFormat.PKCS8,
        encryption_algorithm=serialization.NoEncryption(),
    )
