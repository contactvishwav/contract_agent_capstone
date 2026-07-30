"""
Field-level encryption at rest for stored contract text (P3 item 21).

Scoped deliberately to specific fields (Contract.full_text, Clause.content)
rather than whole-database encryption: Neo4j here is Community Edition (no
enterprise TDE available), and full-database/volume-level encryption only
protects against disk theft - it does nothing once the database is running
and queryable, which is the threat model that actually matters for a live
service. Field-level encryption directly protects the specific data this
item is about, independent of which encryption (if any) exists at the
infrastructure layer.

Key management: no secrets-management infrastructure (AWS/GCP Secrets
Manager, Vault) exists anywhere in this codebase, and no deployment target
is defined yet. IKeyProvider exists so a real secrets manager can be
plugged in later without touching any encrypt/decrypt call site -
EnvKeyProvider (reads ENCRYPTION_KEY, matching how GOOGLE_API_KEY/Neo4j
credentials already work) is the only implementation for now.
"""

import base64
import hashlib
import os
from abc import ABC, abstractmethod

from cryptography.hazmat.primitives.ciphers.aead import AESGCM

from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

# Insecure, hardcoded fallback so local dev/tests work without configuring
# a real key - loudly logged, never silent. Never use this in production;
# set a real ENCRYPTION_KEY instead.
_DEV_DEFAULT_KEY = "insecure-dev-only-encryption-key-do-not-use-in-production"


class IKeyProvider(ABC):
    """Swap in a real secrets manager (AWS/GCP/Vault) by implementing this."""

    @abstractmethod
    def get_key(self) -> bytes:
        """Return a 32-byte key suitable for AES-256-GCM."""


class EnvKeyProvider(IKeyProvider):
    """
    Reads ENCRYPTION_KEY from the environment. Accepts an arbitrary-length
    string (not required to be exactly 32 bytes/base64-encoded raw key
    material) and derives a stable 32-byte AES-256 key from it via SHA-256 -
    ENCRYPTION_KEY is expected to be a generated secret (like the other env-
    var secrets in this codebase), not a human-memorable password, so a
    full password-hashing KDF (PBKDF2/Argon2) isn't needed here.
    """

    def get_key(self) -> bytes:
        key_source = os.getenv("ENCRYPTION_KEY")
        if not key_source:
            logger.warning(
                "ENCRYPTION_KEY not set - using an insecure, hardcoded dev-only "
                "encryption key. Set a real ENCRYPTION_KEY before storing any "
                "real contract data."
            )
            key_source = _DEV_DEFAULT_KEY
        return hashlib.sha256(key_source.encode("utf-8")).digest()


class FieldEncryptor:
    """
    AES-256-GCM encryption for individual text fields. Output is
    base64(nonce || ciphertext-with-auth-tag) as a single string, so it
    round-trips cleanly through Neo4j's string properties.

    decrypt() falls back to returning its input unchanged if it can't be
    decrypted (bad base64, wrong key, or genuinely-plaintext legacy data) -
    a safety net for any data written before encryption was added, not a
    security boundary.
    """

    def __init__(self, key_provider: IKeyProvider = None):
        self._key_provider = key_provider or EnvKeyProvider()

    def encrypt(self, plaintext: str) -> str:
        if not plaintext:
            return plaintext
        aesgcm = AESGCM(self._key_provider.get_key())
        nonce = os.urandom(12)
        ciphertext = aesgcm.encrypt(nonce, plaintext.encode("utf-8"), None)
        return base64.b64encode(nonce + ciphertext).decode("ascii")

    def decrypt(self, encoded: str) -> str:
        if not encoded:
            return encoded
        try:
            raw = base64.b64decode(encoded)
            nonce, ciphertext = raw[:12], raw[12:]
            aesgcm = AESGCM(self._key_provider.get_key())
            return aesgcm.decrypt(nonce, ciphertext, None).decode("utf-8")
        except Exception as e:
            logger.warning(f"Could not decrypt field, returning as-is (legacy plaintext?): {e}")
            return encoded


# Module-level default instance, matching this codebase's existing
# singleton convention (e.g. backend.shared.cache.redis_cache.cache).
field_encryptor = FieldEncryptor()
