"""
Deterministic Redis isolation for the whole test suite (live-infrastructure
audit follow-up).

backend/shared/cache/redis_cache.py's module-level `cache = RedisCache()`
singleton (constructed the moment anything first imports redis_cache.py)
attempts a REAL `redis.from_url(...).ping()` and only falls back to
InMemoryCache on failure - no test ever patched this. Whether a test
exercised the in-memory fallback was pure environmental luck (whether a
real Redis happened to be reachable on localhost:6379 when the suite
happened to run), not a deliberate choice - on a machine with
docker-compose's Redis actually running, this would go live, and
test_llm_extraction_caching.py would break outright (it assumes
.redis_client._cache, an attribute InMemoryCache has and a real redis.Redis
client does not).

Patched here, at conftest module scope, so it takes effect before any test
file in this directory is collected/imported - forcing every
RedisCache._connect() call (including the module-level `cache` singleton)
onto the InMemoryCache fallback path deterministically, regardless of
what's actually reachable on whatever machine runs the suite.
"""

import os

# Celery: tasks run synchronously in-process (Celery's own "eager" test
# mode) - no broker/worker needed for the suite. Must be set before
# anything imports backend.celery_app, since its conf.update(...) reads
# this env var at module-import time.
os.environ.setdefault("CELERY_TASK_ALWAYS_EAGER", "true")

from unittest.mock import patch

import pytest
import redis

from backend.governance.auth import create_access_token

_redis_from_url_patcher = patch.object(
    redis, "from_url",
    side_effect=ConnectionError("Redis disabled for tests - see backend/tests/conftest.py"),
)
_redis_from_url_patcher.start()

# Import *after* the patch above, not before: shared/middleware/
# rate_limit.py's module-level `limiter` singleton probes redis.from_url
# at import time to decide its storage backend (same "construct once,
# fall back to in-memory on failure" shape as RedisCache._connect() above)
# - importing it any earlier would let that probe run unpatched, the
# exact environmental-luck problem this file's docstring describes.
from backend.shared.middleware.rate_limit import reset_rate_limit_storage


@pytest.fixture(autouse=True)
def _reset_rate_limit_storage():
    """The rate limiter's in-memory storage (audit finding #16) persists
    for the entire pytest process otherwise - without this, a test file
    that legitimately calls POST /api/auth/register or /token more than a
    handful of times could get spuriously 429'd by quota consumed by a
    completely unrelated earlier test file, purely depending on
    collection order. Same deterministic-isolation rationale as this
    file's Redis patching above."""
    reset_rate_limit_storage()
    yield


def auth_headers(tenant_id: str = "test_tenant_1", role: str = "ADMIN") -> dict:
    """
    Shared helper (also exposed as the `auth_headers` fixture below) so
    tests don't each hand-build a JWT or the old X-User-Role header - one
    place issues a real, validly-signed token via the same
    create_access_token every route now actually validates against.

        def test_foo(self):
            response = client.get("/api/...", headers=auth_headers(tenant_id="tenant_a", role="ADMIN"))
    """
    token = create_access_token(tenant_id=tenant_id, role=role)
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def make_auth_headers():
    """Fixture form of auth_headers, for tests that prefer fixture
    injection over a direct import: `def test_foo(self, make_auth_headers): ...`"""
    return auth_headers
