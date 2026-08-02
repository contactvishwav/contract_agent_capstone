"""
Tests for GCPSecretManagerKeyProvider and the KEY_PROVIDER-based provider
selection (backend/infrastructure/encryption.py) - added for the GCP
e2-micro deployment (docs/DEPLOYMENT.md). Confirms local dev/CI stay on
EnvKeyProvider by default (KEY_PROVIDER unset), GCP Secret Manager is used
only when explicitly opted into (KEY_PROVIDER=gcp), and
validate_production_key's hard-fail-at-startup guarantee (audit finding
#4) extends correctly to the new provider rather than only ever checking
ENCRYPTION_KEY.
"""

import hashlib
import os
import unittest
from unittest.mock import MagicMock, patch

from backend.infrastructure.encryption import (
    EnvKeyProvider,
    GCPSecretManagerKeyProvider,
    get_key_provider,
    validate_production_key,
    FieldEncryptor,
    _DEV_DEFAULT_KEY,
)


def _fake_secret_manager_client(secret_value: str):
    """A minimal stand-in for google.cloud.secretmanager's
    SecretManagerServiceClient - just enough of access_secret_version's
    response shape (response.payload.data, bytes) to exercise the real
    parsing/derivation code, not a mock of our own logic."""
    client = MagicMock()
    response = MagicMock()
    response.payload.data = secret_value.encode("utf-8")
    client.access_secret_version.return_value = response
    return client


class GCPSecretManagerKeyProviderTests(unittest.TestCase):
    def test_fetches_and_derives_the_same_way_envkeyprovider_does(self):
        """Same derivation contract as EnvKeyProvider - an arbitrary-length
        secret string, SHA-256'd into a 32-byte key - so ciphertext written
        under one provider is decryptable if the same source string is
        ever migrated to the other."""
        fake_client = _fake_secret_manager_client("a-real-generated-secret")
        provider = GCPSecretManagerKeyProvider(project_id="proj-1", secret_id="ENCRYPTION_KEY")

        with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=fake_client):
            key = provider.get_key()

        self.assertEqual(key, hashlib.sha256(b"a-real-generated-secret").digest())
        self.assertEqual(len(key), 32)

    def test_requests_the_correct_secret_version_resource_name(self):
        fake_client = _fake_secret_manager_client("secret-value")
        provider = GCPSecretManagerKeyProvider(project_id="my-project", secret_id="my-secret", version="3")

        with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=fake_client):
            provider.get_key()

        fake_client.access_secret_version.assert_called_once_with(
            request={"name": "projects/my-project/secrets/my-secret/versions/3"}
        )

    def test_defaults_secret_id_to_encryption_key(self):
        provider = GCPSecretManagerKeyProvider(project_id="proj-1")
        self.assertEqual(provider._secret_id, "ENCRYPTION_KEY")

    def test_key_is_cached_not_refetched_on_every_call(self):
        fake_client = _fake_secret_manager_client("secret-value")
        provider = GCPSecretManagerKeyProvider(project_id="proj-1")

        with patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=fake_client):
            provider.get_key()
            provider.get_key()
            provider.get_key()

        fake_client.access_secret_version.assert_called_once()

    def test_missing_project_id_raises_a_clear_error(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("GCP_PROJECT_ID", None)
            provider = GCPSecretManagerKeyProvider()
            with self.assertRaises(RuntimeError) as cm:
                provider.get_key()
            self.assertIn("GCP_PROJECT_ID", str(cm.exception))

    def test_reads_project_and_secret_id_from_env_when_not_passed_explicitly(self):
        with patch.dict(os.environ, {"GCP_PROJECT_ID": "env-project", "GCP_SECRET_ID": "env-secret"}):
            provider = GCPSecretManagerKeyProvider()
            self.assertEqual(provider._project_id, "env-project")
            self.assertEqual(provider._secret_id, "env-secret")


class ProviderSelectionTests(unittest.TestCase):
    """KEY_PROVIDER env var picks the implementation - local dev/CI must
    be completely unaffected by this feature existing at all."""

    def test_unset_key_provider_defaults_to_env(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KEY_PROVIDER", None)
            self.assertIsInstance(get_key_provider(), EnvKeyProvider)

    def test_key_provider_gcp_selects_gcp_provider(self):
        with patch.dict(os.environ, {"KEY_PROVIDER": "gcp", "GCP_PROJECT_ID": "proj-1"}):
            self.assertIsInstance(get_key_provider(), GCPSecretManagerKeyProvider)

    def test_key_provider_is_case_insensitive(self):
        with patch.dict(os.environ, {"KEY_PROVIDER": "GCP", "GCP_PROJECT_ID": "proj-1"}):
            self.assertIsInstance(get_key_provider(), GCPSecretManagerKeyProvider)

    def test_any_other_value_falls_back_to_env(self):
        with patch.dict(os.environ, {"KEY_PROVIDER": "vault"}):
            self.assertIsInstance(get_key_provider(), EnvKeyProvider)

    def test_field_encryptor_default_construction_respects_key_provider(self):
        """The module-level `field_encryptor` singleton (and any bare
        FieldEncryptor()) picks up KEY_PROVIDER automatically - not just
        get_key_provider() in isolation."""
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("KEY_PROVIDER", None)
            self.assertIsInstance(FieldEncryptor()._key_provider, EnvKeyProvider)

        with patch.dict(os.environ, {"KEY_PROVIDER": "gcp", "GCP_PROJECT_ID": "proj-1"}):
            self.assertIsInstance(FieldEncryptor()._key_provider, GCPSecretManagerKeyProvider)


class ValidateProductionKeyGCPProviderTests(unittest.TestCase):
    """validate_production_key (audit finding #4's hard-fail-at-startup
    guarantee) extended to actually validate whichever provider is
    configured, not just ENCRYPTION_KEY."""

    def test_gcp_provider_configured_correctly_passes(self):
        fake_client = _fake_secret_manager_client("a-real-secret")
        with patch.dict(os.environ, {
            "ENVIRONMENT": "production", "KEY_PROVIDER": "gcp", "GCP_PROJECT_ID": "proj-1",
        }), patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=fake_client):
            validate_production_key()  # must not raise

    def test_gcp_provider_missing_project_id_hard_fails(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "KEY_PROVIDER": "gcp"}, clear=False):
            os.environ.pop("GCP_PROJECT_ID", None)
            with self.assertRaises(RuntimeError) as cm:
                validate_production_key()
            self.assertIn("GCPSecretManagerKeyProvider", str(cm.exception))

    def test_gcp_provider_secret_manager_failure_hard_fails(self):
        """A real-world failure mode: wrong project id, secret doesn't
        exist, or the VM's service account lacks IAM permission - all
        surface as an exception from access_secret_version, which must
        become a startup-blocking RuntimeError, not a 500 on first use."""
        broken_client = MagicMock()
        broken_client.access_secret_version.side_effect = Exception("PermissionDenied: caller lacks permission")
        with patch.dict(os.environ, {
            "ENVIRONMENT": "production", "KEY_PROVIDER": "gcp", "GCP_PROJECT_ID": "proj-1",
        }), patch("google.cloud.secretmanager.SecretManagerServiceClient", return_value=broken_client):
            with self.assertRaises(RuntimeError) as cm:
                validate_production_key()
            self.assertIn("failed to produce a key", str(cm.exception))

    def test_env_provider_path_is_unaffected_by_this_change(self):
        """Regression guard: the original EnvKeyProvider behavior/message
        must be byte-for-byte unchanged for the default (non-GCP) path."""
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("KEY_PROVIDER", None)
            os.environ["ENCRYPTION_KEY"] = _DEV_DEFAULT_KEY
            with self.assertRaises(RuntimeError) as cm:
                validate_production_key()
            self.assertIn("ENCRYPTION_KEY", str(cm.exception))


if __name__ == "__main__":
    unittest.main()
