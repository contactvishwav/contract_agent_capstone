"""
API-level rate limiting for the two unauthenticated auth endpoints
(POST /api/auth/token, POST /api/auth/register) - production-readiness
audit finding #16. Both are exposed to unauthenticated callers by design
(there's no token yet to gate them behind), making them the obvious
brute-force/registration-spam target in this system.

Backed by slowapi, using the same Redis deployment already backing
caching/Celery/usage-tracking (REDIS_URL) as its storage - via the
`limits` library's RedisStorage, shared counters across every process
handling these routes, not per-process in-memory state (which would let
each backend replica enforce its own separate quota, defeating the
point).

Redis reachability is probed once at import time, same ping-then-fallback
pattern as shared/cache/redis_cache.py's RedisCache._connect() - but
unlike that class, `limits`' RedisStorage does not gracefully degrade on
its own: a rate-limit check against an unreachable Redis raises
ConnectionError from inside slowapi's request handling, which would 500
every request to these routes instead of just skipping the limit.
Falling back to `limits`' own in-process "memory://" storage keeps the
app usable (un-rate-limited but functional) in dev/CI where Redis isn't
running, matching this codebase's established "Redis optional" pattern
everywhere else.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")

# Overridable via env, same convention as llm_usage_tracker.py's pricing
# constants - defaults chosen to allow normal login retries/typos (a
# handful a minute) while still bounding brute-force/registration-spam
# volume from a single client.
AUTH_TOKEN_RATE_LIMIT = os.getenv("AUTH_TOKEN_RATE_LIMIT", "10/minute")
AUTH_REGISTER_RATE_LIMIT = os.getenv("AUTH_REGISTER_RATE_LIMIT", "5/minute")

# Credential-provisioning endpoints (org invites, MFA) - same brute-force
# rationale as the two above. MFA verify is the obvious code-guessing
# target (a 6-digit TOTP code, 1 in 1,000,000 per guess - rate limiting is
# the real defense here, not the keyspace alone). Invite-accept's token
# has enough entropy that brute-forcing it directly is already infeasible,
# but it's rate-limited anyway as a cheap, free second layer.
AUTH_MFA_VERIFY_RATE_LIMIT = os.getenv("AUTH_MFA_VERIFY_RATE_LIMIT", "10/minute")
AUTH_INVITE_ACCEPT_RATE_LIMIT = os.getenv("AUTH_INVITE_ACCEPT_RATE_LIMIT", "10/minute")


def _resolve_storage_uri() -> str:
    try:
        import redis
        client = redis.from_url(REDIS_URL, socket_connect_timeout=2)
        client.ping()
        logger.info("Rate limiter using Redis-backed storage (shared across processes)")
        return REDIS_URL
    except Exception as e:
        logger.warning(
            f"Rate limiter: Redis unreachable ({e}) - falling back to in-process "
            "storage. Limits will not be shared across processes/replicas until "
            "Redis is reachable."
        )
        return "memory://"


limiter = Limiter(key_func=get_remote_address, storage_uri=_resolve_storage_uri())


def reset_rate_limit_storage() -> None:
    """Clears every tracked key/counter - test isolation hook. `limiter`
    is a module-level singleton (same convention as shared/cache/
    redis_cache.py's `cache`), constructed once per process; in the test
    suite it always falls back to in-process "memory://" storage (real
    Redis is unreachable there by design - see tests/conftest.py), which
    persists for the entire pytest run unless explicitly reset between
    tests, same rationale as that file's own Redis isolation."""
    limiter._limiter.storage.reset()
