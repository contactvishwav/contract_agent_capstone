"""
Regression tests for production-readiness audit finding #16: POST
/api/auth/register and POST /api/auth/token had no rate limiting - the
obvious brute-force/registration-spam target given both are
unauthenticated by necessity.

Backed by slowapi (backend/shared/middleware/rate_limit.py), scoped via
@limiter.limit(...) to these two routes specifically - every other route
is unaffected. tests/conftest.py's autouse `_reset_rate_limit_storage`
fixture resets the shared in-memory storage before each test here (the
same deterministic-isolation rationale as its Redis patching), so these
counts are never polluted by - or leak into - any other test file that
happens to also call these two routes (e.g. test_jwt_auth.py).
"""

import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.api import auth_api
    from backend.shared.middleware.rate_limit import AUTH_REGISTER_RATE_LIMIT, AUTH_TOKEN_RATE_LIMIT

from backend.tests.test_user_repository import FakeUserGraph


class RegisterRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._patcher = patch.object(auth_api._user_repository, "graph", FakeUserGraph())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        # Parsed once so the test doesn't hardcode a number that could
        # silently drift out of sync with the real configured default.
        self.limit = int(AUTH_REGISTER_RATE_LIMIT.split("/")[0])

    def _register(self, i: int):
        return self.client.post(
            "/api/auth/register",
            json={"username": f"user_{i}", "password": "password123", "tenant_id": "tenant_a", "role": "ADMIN"},
        )

    def test_requests_within_the_limit_are_not_rate_limited(self):
        for i in range(self.limit):
            response = self._register(i)
            self.assertNotEqual(response.status_code, 429, f"request {i + 1}/{self.limit} was rate limited too early")

    def test_the_request_past_the_limit_gets_a_real_429(self):
        """The concrete before/after proof: hammer the endpoint past its
        configured threshold and confirm the next request is actually
        rejected with 429, not silently allowed through."""
        for i in range(self.limit):
            self._register(i)

        response = self._register(self.limit)  # one more than the limit allows
        self.assertEqual(response.status_code, 429)
        self.assertIn("rate limit", response.json()["error"].lower())

    def test_rate_limiting_does_not_apply_to_unrelated_routes(self):
        """Scoped to these two routes specifically - the audit finding's
        explicit requirement - not a blanket limit on the whole app."""
        for _ in range(self.limit + 5):
            response = self.client.get("/")
            self.assertNotEqual(response.status_code, 429)


class TokenRateLimitTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)
        self._patcher = patch.object(auth_api._user_repository, "graph", FakeUserGraph())
        self._patcher.start()
        self.addCleanup(self._patcher.stop)
        self.limit = int(AUTH_TOKEN_RATE_LIMIT.split("/")[0])

    def _login_attempt(self):
        # Deliberately wrong credentials - a brute-force attempt is
        # exactly what this limit exists to bound, and the rate-limit
        # check happens before credential verification either way.
        return self.client.post(
            "/api/auth/token",
            json={"username": "nobody", "password": "guess-the-password"},
        )

    def test_requests_within_the_limit_are_not_rate_limited(self):
        for i in range(self.limit):
            response = self._login_attempt()
            self.assertNotEqual(response.status_code, 429, f"request {i + 1}/{self.limit} was rate limited too early")

    def test_the_request_past_the_limit_gets_a_real_429(self):
        for _ in range(self.limit):
            self._login_attempt()

        response = self._login_attempt()
        self.assertEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
