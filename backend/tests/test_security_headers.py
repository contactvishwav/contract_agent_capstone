"""
Regression tests for production-readiness audit finding #15: no security
headers were set on any response (X-Frame-Options, Content-Security-
Policy, X-Content-Type-Options, Strict-Transport-Security). Fixed via
SecurityHeadersMiddleware (backend/shared/middleware/security_headers.py),
applied to every response regardless of route.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.main import app

from fastapi.testclient import TestClient


class SecurityHeadersAreAddedToEveryResponseTests(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(app)

    def test_a_plain_request_gets_the_always_on_headers(self):
        """A request that carries none of these headers itself still
        gets them added to the response - the concrete before/after
        proof requested."""
        response = self.client.get("/")

        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")
        self.assertIn("default-src 'self'", response.headers["content-security-policy"])
        self.assertIn("frame-ancestors 'none'", response.headers["content-security-policy"])

    def test_headers_are_present_even_on_an_error_response(self):
        """A 404 (or any non-2xx) must not silently skip these - the
        middleware wraps every response the app produces."""
        response = self.client.get("/this-route-does-not-exist")

        self.assertEqual(response.status_code, 404)
        self.assertEqual(response.headers["x-content-type-options"], "nosniff")
        self.assertEqual(response.headers["x-frame-options"], "DENY")

    def test_hsts_is_absent_when_the_request_is_plain_http(self):
        """Explicit requirement: don't force HSTS in dev - a request
        with no indication of HTTPS must not get it."""
        response = self.client.get("/")
        self.assertNotIn("strict-transport-security", response.headers)

    def test_hsts_is_present_when_the_request_arrived_over_https(self):
        """Simulates what a real TLS-terminating reverse proxy sets -
        X-Forwarded-Proto: https - the only way this plain-HTTP app can
        know the original client connection was actually HTTPS."""
        response = self.client.get("/", headers={"X-Forwarded-Proto": "https"})

        self.assertIn("strict-transport-security", response.headers)
        self.assertIn("max-age=", response.headers["strict-transport-security"])


if __name__ == "__main__":
    unittest.main()
