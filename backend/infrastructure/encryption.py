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

Key management: IKeyProvider exists so a real secrets manager can be
plugged in without touching any encrypt/decrypt call site. Two
implementations:
- EnvKeyProvider (reads ENCRYPTION_KEY, matching how GOOGLE_API_KEY/Neo4j
  credentials already work) - the default everywhere: local dev, CI, and
  any deployment target that hasn't opted into a real secrets manager.
- GCPSecretManagerKeyProvider - real backing store for the GCP e2-micro
  deployment specifically (docs/DEPLOYMENT.md), selected via
  KEY_PROVIDER=gcp. Local dev/CI are completely unaffected: KEY_PROVIDER
  unset (or anything other than "gcp") keeps EnvKeyProvider as the
  default, exactly as before this was added.
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


class GCPSecretManagerKeyProvider(IKeyProvider):
    """
    Fetches the encryption key's source material from GCP Secret Manager
    instead of a plain environment variable - real backing store for the
    GCP e2-micro deployment (docs/DEPLOYMENT.md), where a secret sitting
    in a docker-compose env var on disk is a weaker guarantee than this
    VM's other secrets get.

    Requires GCP_PROJECT_ID (the GCP project) and GCP_SECRET_ID (the
    Secret Manager secret's resource name, not its value - defaults to
    "ENCRYPTION_KEY" so a deployment doesn't need to invent a second
    name for the same concept). Authenticates via Application Default
    Credentials - the VM's attached service account in the real
    deployment, so no credential JSON file is read or stored by this
    code at all.

    The fetched secret payload is treated exactly like EnvKeyProvider's
    ENCRYPTION_KEY value - an arbitrary-length string, SHA-256'd into a
    stable 32-byte AES-256 key, not required to already be exactly 32
    raw bytes.

    The key is fetched once and cached for the life of this instance
    (not re-fetched per encrypt/decrypt call): Secret Manager access is a
    billed, quota-limited API call, and the key never changes mid-process
    - the module-level `field_encryptor` singleton this backs lives for
    the whole process anyway.
    """

    def __init__(self, project_id: str = None, secret_id: str = None, version: str = "latest"):
        self._project_id = project_id or os.getenv("GCP_PROJECT_ID")
        self._secret_id = secret_id or os.getenv("GCP_SECRET_ID", "ENCRYPTION_KEY")
        self._version = version
        self._cached_key: bytes = None

    def get_key(self) -> bytes:
        if self._cached_key is not None:
            return self._cached_key

        if not self._project_id:
            raise RuntimeError(
                "GCP_PROJECT_ID is not set - required when KEY_PROVIDER=gcp "
                "(GCPSecretManagerKeyProvider needs it to address the secret)."
            )

        from google.cloud import secretmanager

        client = secretmanager.SecretManagerServiceClient()
        name = f"projects/{self._project_id}/secrets/{self._secret_id}/versions/{self._version}"
        response = client.access_secret_version(request={"name": name})
        key_source = response.payload.data.decode("utf-8")

        self._cached_key = hashlib.sha256(key_source.encode("utf-8")).digest()
        return self._cached_key


def get_key_provider() -> IKeyProvider:
    """
    Selects the IKeyProvider implementation via the KEY_PROVIDER env var -
    "gcp" for GCPSecretManagerKeyProvider (the real backing store for the
    GCP e2-micro deployment), anything else (including unset, the default
    everywhere else - local dev, CI, any other deployment target) for
    EnvKeyProvider. Called once to build the module-level `field_encryptor`
    singleton below; also the single place a future third provider would
    be wired in.
    """
    if os.getenv("KEY_PROVIDER", "env").strip().lower() == "gcp":
        return GCPSecretManagerKeyProvider()
    return EnvKeyProvider()


def validate_production_key() -> None:
    """
    Production-readiness audit finding #4 (see governance/auth.py's
    validate_production_secret for the matching JWT-side fix - same
    issue, same fix shape): the fallback above is loudly logged, but only
    logged. Call once at application startup (backend/main.py's
    lifespan), not lazily on first encrypt/decrypt - a misconfigured
    production deployment should never start accepting traffic, let alone
    silently encrypt real contract data with a key published in this
    repo's own source. No-ops entirely outside production.

    Provider-aware: EnvKeyProvider gets the original direct env-var check
    (unchanged behavior/message). Any other provider (GCPSecretManagerKeyProvider
    today) gets validated by actually calling get_key() - a bad
    GCP_PROJECT_ID/GCP_SECRET_ID/IAM permissions problem should fail loudly
    at startup, the same "don't start accepting traffic" guarantee, not
    surface as a 500 on the first real encrypt/decrypt mid-request.
    """
    from backend.shared.utils.route_utils import is_production

    if not is_production():
        return

    provider = get_key_provider()

    if isinstance(provider, EnvKeyProvider):
        key_source = os.getenv("ENCRYPTION_KEY")
        if not key_source or key_source == _DEV_DEFAULT_KEY:
            raise RuntimeError(
                "ENCRYPTION_KEY is not set (or is set to the insecure dev-only "
                "default) while ENVIRONMENT=production. Refusing to start - set "
                "a real ENCRYPTION_KEY before deploying to production."
            )
        return

    try:
        provider.get_key()
    except Exception as e:
        raise RuntimeError(
            f"Configured key provider ({type(provider).__name__}) failed to "
            f"produce a key while ENVIRONMENT=production. Refusing to start: {e}"
        ) from e


class DecryptionError(Exception):
    """Raised when a stored field can't be decrypted - wrong/rotated key,
    corrupted data, or a tampered ciphertext. Deliberately distinct from a
    bare Exception so callers that need to react to a decrypt failure
    specifically (as opposed to any other error) can catch this one type."""


class FieldEncryptor:
    """
    AES-256-GCM encryption for individual text fields. Output is
    base64(nonce || ciphertext-with-auth-tag) as a single string, so it
    round-trips cleanly through Neo4j's string properties.

    decrypt() raises DecryptionError on any failure (bad base64, wrong key,
    or a corrupted/tampered ciphertext) rather than silently returning the
    input unchanged. There is no legacy-plaintext fallback: every write
    site for every encrypted field (Contract.full_text, Clause.content,
    Chunk.content/DocumentChunk.content - P3 item 21 and its follow-up)
    has encrypted before persisting since the field was introduced, so
    there is no real plaintext-written-before-encryption data this system
    has ever needed to tolerate. A silent pass-through here would make a
    genuinely corrupted or tampered field indistinguishable from a
    successful decrypt of garbage bytes - the opposite of what encryption
    at rest is supposed to guarantee.
    """

    def __init__(self, key_provider: IKeyProvider = None):
        self._key_provider = key_provider or get_key_provider()

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
            logger.error(f"Failed to decrypt field - corrupted, tampered, or wrong key: {e}")
            raise DecryptionError(f"Could not decrypt field: {e}") from e


# Module-level default instance, matching this codebase's existing
# singleton convention (e.g. backend.shared.cache.redis_cache.cache).
# key_provider resolved via get_key_provider() (KEY_PROVIDER env var) -
# EnvKeyProvider everywhere by default, GCPSecretManagerKeyProvider only
# where KEY_PROVIDER=gcp is explicitly set.
field_encryptor = FieldEncryptor()
