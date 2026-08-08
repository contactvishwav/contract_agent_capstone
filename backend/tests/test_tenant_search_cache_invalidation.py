from backend.shared.cache.redis_cache import InMemoryCache, RedisCache


def test_search_cache_invalidation_removes_only_the_target_tenant():
    memory = InMemoryCache()
    memory.set("vector_search:tenant_a:tool:one", "1")
    memory.set("vector_search:tenant_a:rest:two", "2")
    memory.set("vector_search:tenant_b:tool:three", "3")
    memory.set("unrelated", "4")
    cache = RedisCache.__new__(RedisCache)
    cache.redis_client = memory

    assert cache.invalidate_tenant_search("tenant_a") == 2
    assert memory.get("vector_search:tenant_a:tool:one") is None
    assert memory.get("vector_search:tenant_b:tool:three") == "3"
    assert memory.get("unrelated") == "4"
