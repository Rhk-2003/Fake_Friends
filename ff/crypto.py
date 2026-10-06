"""Encryption at rest + password hashing.

* Blobs are AES-256-GCM. Each blob gets its own key, derived from the master
  key with HKDF using the blob's logical path, and the path is also bound in
  as associated data - so a blob cannot be swapped for another one.
* File names in the (public) data repo are HMACs of the logical path, so quiz
  codes cannot be enumerated by browsing the repository.
* Passwords are hashed with scrypt (never stored, never reversible).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF

MAGIC = b"FF1"
_NONCE = 12


class CryptoError(RuntimeError):
    pass


def generate_master_key() -> str:
    return base64.urlsafe_b64encode(secrets.token_bytes(32)).decode()


def normalize_master_key(raw: str) -> bytes:
    """Accept a 32-byte urlsafe-base64 key, or stretch any long passphrase."""
    raw = (raw or "").strip()
    if len(raw) < 24:
        raise CryptoError("FF_MASTER_KEY is missing or too short (need 24+ characters).")
    try:
        decoded = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        if len(decoded) == 32:
            return decoded
    except Exception:
        pass
    return hashlib.scrypt(raw.encode(), salt=b"fake-friends/master", n=2**14, r=8, p=1, dklen=32)


def _subkey(master: bytes, purpose: str) -> bytes:
    return HKDF(algorithm=hashes.SHA256(), length=32, salt=None, info=purpose.encode()).derive(master)


def encrypt(master: bytes, path: str, plaintext: bytes) -> bytes:
    nonce = os.urandom(_NONCE)
    ct = AESGCM(_subkey(master, "enc:" + path)).encrypt(nonce, plaintext, path.encode())
    return MAGIC + nonce + ct


def decrypt(master: bytes, path: str, blob: bytes) -> bytes:
    if not blob.startswith(MAGIC) or len(blob) < len(MAGIC) + _NONCE + 16:
        raise CryptoError("Stored data is not in the expected encrypted format.")
    nonce = blob[len(MAGIC):len(MAGIC) + _NONCE]
    try:
        return AESGCM(_subkey(master, "enc:" + path)).decrypt(
            nonce, blob[len(MAGIC) + _NONCE:], path.encode()
        )
    except InvalidTag as exc:
        raise CryptoError(
            "Could not decrypt stored data - FF_MASTER_KEY does not match the key it was saved with."
        ) from exc


def opaque_name(master: bytes, path: str) -> str:
    """Stable, non-reversible file name for a logical path."""
    return hmac.new(_subkey(master, "name"), path.encode(), hashlib.sha256).hexdigest()[:40]


# ---- passwords -------------------------------------------------------------
def hash_password(password: str) -> str:
    salt = os.urandom(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
    return "scrypt$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(digest).decode()


def verify_password(password: str, stored: str) -> bool:
    try:
        scheme, salt_b64, digest_b64 = stored.split("$")
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(digest_b64)
        actual = hashlib.scrypt(password.encode(), salt=salt, n=2**14, r=8, p=1, dklen=32)
        return hmac.compare_digest(actual, expected)
    except Exception:
        return False


def constant_time_equal(a: str, b: str) -> bool:
    return hmac.compare_digest(a.encode(), b.encode())
