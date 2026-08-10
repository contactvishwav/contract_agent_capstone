"""
Regression tests for the reconciliation-audit finding: POST /api/run/
(Contract Chat) had no rate limiting at all, unlike every billed/abuse-
sensitive endpoint elsewhere in this codebase. See
backend/shared/middleware/rate_limit.py's module docstring for the
cost rationale and CHAT_RUN_RATE_LIMIT's value.

Requests use an unrecognized model id so the real request body reaches
@limiter.limit's check (which runs first, inside run()'s own call) and
then fails fast and cheaply with 400 from validate_model() - no real LLM
call, matching test_auth_rate_limiting.py's "cheap failing request" style.
tests/conftest.py's autouse _reset_rate_limit_storage fixture keeps this
file isolated from every other test file's rate-limit consumption.
"""

import unittest
from unittest.mock import MagicMock, patch

from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app
    from backend.shared.middleware.rate_limit import CHAT_RUN_RATE_LIMIT

from backend.tests.conftest import auth_headers


class ChatRunRateLimitTests(unittest.TestCase):
    def setUp(self):
        # get_llm_manager reads request.app.state.llm_manager, normally set
        # by the app's lifespan startup hook - which plain TestClient(app)
        # (no `with`) never triggers. validate_model() rejects the test's
        # unknown model id before the route body ever touches llm_mgr, so
        # any placeholder object here is enough to satisfy the dependency.
        app.state.llm_manager = MagicMock()
        self.client = TestClient(app)
        self.limit = int(CHAT_RUN_RATE_LIMIT.split("/")[0])

    def _run(self, tenant_id: str = "tenant_a"):
        return self.client.post(
            "/api/run/",
            json={"model": "not-a-real-model", "prompt": "hi", "history": "[]"},
            headers=auth_headers(tenant_id=tenant_id),
        )

    def test_requests_within_the_limit_are_not_rate_limited(self):
        for i in range(self.limit):
            response = self._run()
            self.assertNotEqual(response.status_code, 429, f"request {i + 1}/{self.limit} was rate limited too early")
            # Confirms these calls actually reached and exercised the real
            # route body (validate_model's 400), not some unrelated failure.
            self.assertEqual(response.status_code, 400)

    def test_the_request_past_the_limit_gets_a_real_429(self):
        """The concrete before/after proof: hammer the endpoint past its
        configured threshold and confirm the next request is actually cut
        off with 429, not silently allowed through to hit a billed LLM."""
        for _ in range(self.limit):
            self._run()

        response = self._run()  # one more than the limit allows
        self.assertEqual(response.status_code, 429)
        self.assertIn("rate limit", response.json()["error"].lower())

    def test_limit_is_scoped_per_tenant_not_shared_globally(self):
        """Design intent (rate_limit.py's tenant_scoped_or_ip_key): one
        tenant exhausting its own quota must not throttle a different
        tenant's legitimate, unrelated chat traffic."""
        for _ in range(self.limit):
            response = self._run(tenant_id="tenant_a")
            self.assertNotEqual(response.status_code, 429)
        exhausted = self._run(tenant_id="tenant_a")
        self.assertEqual(exhausted.status_code, 429)

        other_tenant_response = self._run(tenant_id="tenant_b")
        self.assertNotEqual(other_tenant_response.status_code, 429)
        self.assertEqual(other_tenant_response.status_code, 400)

    def test_rate_limiting_does_not_apply_to_unrelated_routes(self):
        for _ in range(self.limit + 5):
            response = self.client.get("/")
            self.assertNotEqual(response.status_code, 429)


if __name__ == "__main__":
    unittest.main()
