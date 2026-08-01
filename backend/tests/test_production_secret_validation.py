"""
Regression tests for production-readiness audit finding #4:
JWT_SECRET_KEY/ENCRYPTION_KEY falling back to a hardcoded, insecure,
published-in-this-repo default was only ever logged (backend/governance/
auth.py, backend/infrastructure/encryption.py) - a real deployment that
forgot either env var would run indefinitely, silently insecure, in any
environment including production.

validate_production_secret()/validate_production_key() now hard-fail
(RuntimeError) at application startup when ENVIRONMENT=production and
either secret is unset or still equal to its dev-only default - before
the app accepts a single request, not lazily on first use.
"""

import os
import unittest
from unittest.mock import patch


class ValidateProductionSecretTests(unittest.TestCase):
    def setUp(self):
        from backend.governance.auth import validate_production_secret, _DEV_DEFAULT_SECRET
        self.validate = validate_production_secret
        self.dev_default = _DEV_DEFAULT_SECRET

    def test_noop_outside_production_even_if_unset(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            os.environ.pop("JWT_SECRET_KEY", None)
            self.validate()  # must not raise

    def test_raises_in_production_when_unset(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("JWT_SECRET_KEY", None)
            with self.assertRaises(RuntimeError) as cm:
                self.validate()
            self.assertIn("JWT_SECRET_KEY", str(cm.exception))

    def test_raises_in_production_when_still_the_dev_default(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "JWT_SECRET_KEY": self.dev_default}):
            with self.assertRaises(RuntimeError):
                self.validate()

    def test_passes_in_production_with_a_real_secret_set(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "JWT_SECRET_KEY": "a-real-generated-secret"}):
            self.validate()  # must not raise


class ValidateProductionKeyTests(unittest.TestCase):
    def setUp(self):
        from backend.infrastructure.encryption import validate_production_key, _DEV_DEFAULT_KEY
        self.validate = validate_production_key
        self.dev_default = _DEV_DEFAULT_KEY

    def test_noop_outside_production_even_if_unset(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "development"}, clear=False):
            os.environ.pop("ENCRYPTION_KEY", None)
            self.validate()  # must not raise

    def test_raises_in_production_when_unset(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("ENCRYPTION_KEY", None)
            with self.assertRaises(RuntimeError) as cm:
                self.validate()
            self.assertIn("ENCRYPTION_KEY", str(cm.exception))

    def test_raises_in_production_when_still_the_dev_default(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "ENCRYPTION_KEY": self.dev_default}):
            with self.assertRaises(RuntimeError):
                self.validate()

    def test_passes_in_production_with_a_real_key_set(self):
        with patch.dict(os.environ, {"ENVIRONMENT": "production", "ENCRYPTION_KEY": "a-real-generated-key"}):
            self.validate()  # must not raise


class ApplicationStartupHardFailsInProductionTests(unittest.TestCase):
    """The concrete before/after proof: actually construct/start the real
    FastAPI app (via TestClient's context-manager form, which triggers
    the real lifespan startup) with ENVIRONMENT=production and no
    secrets configured, and confirm the app refuses to start."""

    def test_app_startup_raises_when_production_and_secrets_unset(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.main import app

        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {"ENVIRONMENT": "production"}, clear=False):
            os.environ.pop("JWT_SECRET_KEY", None)
            os.environ.pop("ENCRYPTION_KEY", None)
            with self.assertRaises(RuntimeError):
                with TestClient(app):
                    pass  # lifespan startup runs on entering this context

    def test_app_startup_succeeds_when_production_and_secrets_set(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.main import app

        from fastapi.testclient import TestClient

        with patch.dict(os.environ, {
            "ENVIRONMENT": "production",
            "JWT_SECRET_KEY": "a-real-generated-secret",
            "ENCRYPTION_KEY": "a-real-generated-key",
        }):
            with TestClient(app):
                pass  # must not raise


if __name__ == "__main__":
    unittest.main()
