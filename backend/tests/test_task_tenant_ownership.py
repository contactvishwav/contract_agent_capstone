import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException

from backend.governance.auth import TokenIdentity
from backend.infrastructure.task_ownership import (
    TaskOwnershipConflict,
    TaskOwnershipStore,
    TaskOwnershipUnavailable,
)


class FakeRedis:
    def __init__(self):
        self.values = {}
        self.fail = False

    def set(self, key, value, ex=None, nx=False):
        if self.fail:
            raise ConnectionError("redis unavailable")
        if nx and key in self.values:
            return False
        self.values[key] = value
        return True

    def get(self, key):
        if self.fail:
            raise ConnectionError("redis unavailable")
        return self.values.get(key)

    def delete(self, key):
        self.values.pop(key, None)
        return 1


class TaskOwnershipStoreTests(unittest.TestCase):
    def setUp(self):
        self.redis = FakeRedis()
        self.store = TaskOwnershipStore(self.redis, environment="test", ttl_seconds=60)

    def test_owner_can_read_but_other_tenant_and_unknown_cannot(self):
        self.store.claim("task-1", "tenant_a")
        self.assertTrue(self.store.is_owner("task-1", "tenant_a"))
        self.assertFalse(self.store.is_owner("task-1", "tenant_b"))
        self.assertFalse(self.store.is_owner("unknown", "tenant_a"))

    def test_corrupt_record_fails_closed(self):
        self.redis.values[self.store.key("task-1", "tenant_a")] = "corrupt"
        self.assertFalse(self.store.is_owner("task-1", "tenant_a"))

    def test_expired_marker_fails_closed(self):
        self.store.claim("task-1", "tenant_a")
        self.redis.values.pop(self.store.key("task-1", "tenant_a"))
        self.assertFalse(self.store.is_owner("task-1", "tenant_a"))

    def test_redis_unavailable_fails_closed(self):
        self.redis.fail = True
        with self.assertRaises(TaskOwnershipUnavailable):
            self.store.is_owner("task-1", "tenant_a")

    def test_duplicate_claim_does_not_overwrite(self):
        self.store.claim("task-1", "tenant_a")
        with self.assertRaises(TaskOwnershipConflict):
            self.store.claim("task-1", "tenant_a")

    def test_enqueue_claims_before_publish_and_releases_on_publish_failure(self):
        task = MagicMock()
        task.apply_async.side_effect = RuntimeError("broker unavailable")
        with patch("backend.infrastructure.task_ownership.uuid.uuid4") as mock_uuid:
            mock_uuid.return_value.hex = "fixed-task-id"
            with self.assertRaises(RuntimeError):
                self.store.enqueue(task, "tenant_a", ("c1", "tenant_a"))
        self.assertFalse(self.store.is_owner("fixed-task-id", "tenant_a"))


class TaskStatusBoundaryTests(unittest.IsolatedAsyncioTestCase):
    async def test_other_tenant_and_unknown_task_share_the_same_response(self):
        import backend.api.contract_intelligence as api

        tenant_b = TokenIdentity(tenant_id="tenant_b", role="ADMIN")
        for task_id in ("tenant-a-task", "unknown-task"):
            with patch.object(api.task_ownership_store, "is_owner", return_value=False):
                with self.assertRaises(HTTPException) as caught:
                    await api.get_analysis_task_status(task_id, identity=tenant_b)
            self.assertEqual(caught.exception.status_code, 404)
            self.assertEqual(caught.exception.detail, "Task not found")

    async def test_redis_unavailable_returns_service_unavailable_without_reading_celery(self):
        import backend.api.contract_intelligence as api

        identity = TokenIdentity(tenant_id="tenant_a", role="ADMIN")
        with patch.object(
            api.task_ownership_store, "is_owner", side_effect=TaskOwnershipUnavailable("down")
        ), patch("celery.result.AsyncResult") as async_result:
            with self.assertRaises(HTTPException) as caught:
                await api.get_analysis_task_status("task-1", identity=identity)
        self.assertEqual(caught.exception.status_code, 503)
        async_result.assert_not_called()

if __name__ == "__main__":
    unittest.main()
