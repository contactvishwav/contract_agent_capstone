import json
import hashlib
import logging
import threading
from typing import Any, Optional, Dict
from functools import wraps
import os

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

class RedisCache:
    """Redis-based caching for CUAD analysis results"""
    
    def __init__(self):
        self.redis_client = None
        self._connect()
    
    def _connect(self):
        """Connect to Redis with fallback to in-memory cache"""
        try:
            import redis
            redis_url = os.getenv("REDIS_URL", "redis://localhost:6379")
            self.redis_client = redis.from_url(redis_url, decode_responses=True)
            self.redis_client.ping()
            logger.info("Connected to Redis cache")
        except Exception as e:
            logger.warning(f"Redis connection failed, using in-memory cache: {e}")
            self.redis_client = InMemoryCache()
    
    def get(self, key: str) -> Optional[Any]:
        """Get cached value"""
        try:
            value = self.redis_client.get(key)
            return json.loads(value) if value else None
        except Exception as e:
            logger.error(f"Cache get failed for key {key}: {e}")
            return None
    
    def set(self, key: str, value: Any, ttl: int = 3600) -> bool:
        """Set cached value with TTL"""
        try:
            serialized = json.dumps(value, default=str)
            return self.redis_client.setex(key, ttl, serialized)
        except Exception as e:
            logger.error(f"Cache set failed for key {key}: {e}")
            return False
    
    def generate_key(self, prefix: str, *args) -> str:
        """Generate cache key from arguments"""
        key_data = f"{prefix}:{':'.join(str(arg) for arg in args)}"
        return hashlib.md5(key_data.encode()).hexdigest()

class InMemoryCache:
    """
    Fallback in-memory cache. Also backs LLMUsageTracker's counters
    (shared/monitoring/llm_usage_tracker.py), hallucination_tracker.py's
    counters, latency_tracker.py's duration samples, and circuit_breaker.py's
    state when no real Redis is reachable - incr/incrby/incrbyfloat/sadd/
    smembers/rpush/ltrim/lrange/set mirror redis-py's actual return types
    (int/int/float/int/set/int/bool/list of str/bool) closely enough that
    tracker code doesn't need to know which backend it's talking to.

    Everything lives in one dict so a blanket `._cache.clear()` (the
    reset already used throughout this test suite) really does clear
    all state, counters included - not just the string-keyed cache
    entries.
    """

    def __init__(self):
        self._cache: Dict[str, Any] = {}
        self._lock = threading.Lock()

    def get(self, key: str) -> Optional[str]:
        return self._cache.get(key)

    def set(self, key: str, value: Any) -> bool:
        with self._lock:
            self._cache[key] = value
        return True

    def setex(self, key: str, ttl: int, value: str) -> bool:
        self._cache[key] = value
        return True

    def ping(self):
        return True

    def incr(self, key: str, amount: int = 1) -> int:
        with self._lock:
            value = int(self._cache.get(key, 0) or 0) + amount
            self._cache[key] = value
            return value

    def incrby(self, key: str, amount: int) -> int:
        return self.incr(key, amount)

    def incrbyfloat(self, key: str, amount: float) -> float:
        with self._lock:
            value = float(self._cache.get(key, 0.0) or 0.0) + amount
            self._cache[key] = value
            return value

    def sadd(self, key: str, *values) -> int:
        with self._lock:
            existing = self._cache.get(key)
            current = existing if isinstance(existing, set) else set()
            before = len(current)
            current.update(values)
            self._cache[key] = current
            return len(current) - before

    def smembers(self, key: str) -> set:
        value = self._cache.get(key)
        return set(value) if isinstance(value, set) else set()

    def rpush(self, key: str, *values) -> int:
        with self._lock:
            existing = self._cache.get(key)
            current = existing if isinstance(existing, list) else []
            current.extend(str(v) for v in values)
            self._cache[key] = current
            return len(current)

    def ltrim(self, key: str, start: int, end: int) -> bool:
        with self._lock:
            existing = self._cache.get(key)
            if isinstance(existing, list):
                # Redis LTRIM's `end` is inclusive; Python slicing's isn't -
                # +1 it, except -1 ("to the end") which has no direct +1
                # equivalent and must stay a bare slice.
                self._cache[key] = existing[start:] if end == -1 else existing[start:end + 1]
            return True

    def publish(self, channel: str, message: str) -> int:
        # No real pub/sub without real Redis - see progress_publisher.py's
        # module docstring for why this is a safe no-op (returns 0
        # subscribers) rather than raising. Live per-step progress simply
        # isn't available in this degraded fallback mode; every other
        # real feature that reads the published result afterward
        # (node_status, the audit trail, quality_grade) is unaffected.
        return 0

    def pubsub(self):
        return _NullPubSub()

    def lrange(self, key: str, start: int, end: int) -> list:
        existing = self._cache.get(key)
        if not isinstance(existing, list):
            return []
        return existing[start:] if end == -1 else existing[start:end + 1]


class _NullPubSub:
    """Stand-in for redis-py's PubSub object when running against the
    InMemoryCache fallback (no real Redis reachable) - subscribing works
    but get_message always reports nothing available, since there is no
    real cross-process channel to deliver on."""

    def subscribe(self, *channels, **kwargs):
        pass

    def get_message(self, timeout: Optional[float] = None, **kwargs) -> Optional[dict]:
        return None

    def close(self):
        pass

# Global cache instance
cache = RedisCache()

def cache_result(prefix: str, ttl: int = 3600):
    """Decorator for caching function results"""
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            cache_key = cache.generate_key(prefix, *args, *kwargs.values())
            
            cached_result = cache.get(cache_key)
            if cached_result is not None:
                return cached_result
            
            result = func(*args, **kwargs)
            cache.set(cache_key, result, ttl)
            return result
        return wrapper
    return decorator