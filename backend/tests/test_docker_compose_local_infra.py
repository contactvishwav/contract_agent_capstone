"""
Regression test for P3 item 17: docker-compose.yml never provisioned a
Neo4j or Redis service - the backend defaulted to the public shared demo
instance (neo4j+s://demo.neo4jlabs.com) and CACHE_ENABLED/RedisCache
silently degraded to an in-memory-only cache (confirmed dead in the
original audit) since no Redis was ever reachable.

This doesn't test container behavior (no Docker engine assumed in the test
environment) - it parses docker-compose.yml itself and asserts the services
and defaults that would catch a regression back to depending on the shared
public demo instance with no local Neo4j/Redis.
"""

import os
import unittest

import yaml

COMPOSE_PATH = os.path.join(os.path.dirname(__file__), "..", "..", "docker-compose.yml")


def load_compose():
    with open(COMPOSE_PATH, encoding="utf-8") as f:
        return yaml.safe_load(f)


class DockerComposeLocalInfraTests(unittest.TestCase):
    def setUp(self):
        self.compose = load_compose()
        self.services = self.compose.get("services", {})

    def test_neo4j_service_exists(self):
        self.assertIn("neo4j", self.services)

    def test_redis_service_exists(self):
        self.assertIn("redis", self.services)

    def test_neo4j_service_uses_a_real_image_with_a_persistent_volume(self):
        neo4j = self.services["neo4j"]
        self.assertIn("image", neo4j)
        self.assertTrue(neo4j["image"].startswith("neo4j:"))
        self.assertTrue(any("neo4j_data" in v for v in neo4j.get("volumes", [])))

    def test_redis_service_uses_a_real_image_with_a_persistent_volume(self):
        redis = self.services["redis"]
        self.assertIn("image", redis)
        # redis/redis-stack-server, not plain redis:* (Phase 4 HITL) -
        # langgraph-checkpoint-redis's RedisSaver needs the RediSearch
        # module (FT.* commands), which plain Redis doesn't ship.
        self.assertTrue(redis["image"].startswith("redis"))
        self.assertTrue(any("redis_data" in v for v in redis.get("volumes", [])))

    def test_backend_no_longer_defaults_to_the_shared_public_demo_instance(self):
        backend_env = self.services["backend"]["environment"]
        neo4j_uri_entry = next(e for e in backend_env if e.startswith("NEO4J_URI="))
        self.assertNotIn(
            "demo.neo4jlabs.com", neo4j_uri_entry,
            "backend's default NEO4J_URI must not point at the shared public demo instance",
        )

    def test_backend_default_redis_url_points_at_the_local_compose_service(self):
        backend_env = self.services["backend"]["environment"]
        redis_url_entry = next((e for e in backend_env if e.startswith("REDIS_URL=")), None)
        self.assertIsNotNone(redis_url_entry, "backend service must configure REDIS_URL")
        self.assertIn("redis:6379", redis_url_entry)

    def test_backend_depends_on_neo4j_and_redis(self):
        depends_on = self.services["backend"].get("depends_on", {})
        self.assertIn("neo4j", depends_on)
        self.assertIn("redis", depends_on)

    def test_neo4j_and_redis_have_healthchecks(self):
        self.assertIn("healthcheck", self.services["neo4j"])
        self.assertIn("healthcheck", self.services["redis"])


if __name__ == "__main__":
    unittest.main()
