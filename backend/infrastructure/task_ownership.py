"""Cross-process tenant ownership for Celery task identifiers.

Celery's result backend intentionally treats an unknown id as ``PENDING``;
it does not record which authenticated tenant submitted a task.  Possession
of a task id therefore cannot authorize result access.  This module stores a
separate, short-lived ownership marker in real Redis before a task is
published.

The in-process cache fallback is deliberately rejected.  It is useful for
best-effort metrics and caches, but cannot enforce authorization across the
FastAPI and worker processes.  Ownership checks fail closed when Redis is not
available.
"""

from __future__ import annotations

import hashlib
import os
import uuid
from typing import Any, Iterable, Optional

from backend.shared.cache.redis_cache import InMemoryCache, cache


DEFAULT_TASK_OWNERSHIP_TTL_SECONDS = 86400
_VALUE = "v1"


class TaskOwnershipUnavailable(RuntimeError):
    """Real shared ownership storage is unavailable or returned bad data."""


class TaskOwnershipConflict(RuntimeError):
    """The generated task identifier already has an ownership marker."""


class TaskOwnershipStore:
    def __init__(
        self,
        redis_client: Optional[Any] = None,
        environment: Optional[str] = None,
        ttl_seconds: Optional[int] = None,
    ) -> None:
        self._explicit_client = redis_client
        self.environment = environment or os.getenv("ENVIRONMENT", "development")
        self.ttl_seconds = ttl_seconds or int(
            os.getenv("TASK_OWNERSHIP_TTL_SECONDS", str(DEFAULT_TASK_OWNERSHIP_TTL_SECONDS))
        )

    @property
    def redis_client(self):
        return self._explicit_client if self._explicit_client is not None else cache.redis_client

    @staticmethod
    def _digest(value: str) -> str:
        return hashlib.sha256(value.encode("utf-8")).hexdigest()[:24]

    def key(self, task_id: str, tenant_id: str) -> str:
        """Environment/purpose/tenant/id namespace without raw identifiers."""
        return (
            f"contract-agent:{self.environment}:task-owner:"
            f"{self._digest(tenant_id)}:{self._digest(task_id)}"
        )

    def _shared_client(self):
        client = self.redis_client
        if client is None or isinstance(client, InMemoryCache):
            raise TaskOwnershipUnavailable("real Redis is required for task ownership")
        return client

    def claim(self, task_id: str, tenant_id: str) -> None:
        if not task_id or not tenant_id:
            raise ValueError("task_id and authenticated tenant_id are required")
        try:
            claimed = self._shared_client().set(
                self.key(task_id, tenant_id), _VALUE, ex=self.ttl_seconds, nx=True
            )
        except TaskOwnershipUnavailable:
            raise
        except Exception as exc:
            raise TaskOwnershipUnavailable("task ownership storage is unavailable") from exc
        if not claimed:
            raise TaskOwnershipConflict("task ownership marker already exists")

    def is_owner(self, task_id: str, tenant_id: str) -> bool:
        if not task_id or not tenant_id:
            return False
        try:
            value = self._shared_client().get(self.key(task_id, tenant_id))
        except TaskOwnershipUnavailable:
            raise
        except Exception as exc:
            raise TaskOwnershipUnavailable("task ownership storage is unavailable") from exc
        if isinstance(value, bytes):
            value = value.decode("utf-8", errors="replace")
        # Missing and corrupt records both fail closed and are intentionally
        # indistinguishable from a task owned by another tenant.
        return value == _VALUE

    def release(self, task_id: str, tenant_id: str) -> None:
        try:
            self._shared_client().delete(self.key(task_id, tenant_id))
        except Exception:
            # Best effort after enqueue failure.  The marker expires even if
            # Redis fails between SET and cleanup.
            return

    def enqueue(self, task: Any, tenant_id: str, args: Iterable[Any]):
        """Reserve ownership before publishing and roll it back on failure."""
        task_id = uuid.uuid4().hex
        self.claim(task_id, tenant_id)
        try:
            return task.apply_async(args=tuple(args), task_id=task_id)
        except Exception:
            self.release(task_id, tenant_id)
            raise


task_ownership_store = TaskOwnershipStore()
