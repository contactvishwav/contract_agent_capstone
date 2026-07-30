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

from unittest.mock import patch

import redis

_redis_from_url_patcher = patch.object(
    redis, "from_url",
    side_effect=ConnectionError("Redis disabled for tests - see backend/tests/conftest.py"),
)
_redis_from_url_patcher.start()
