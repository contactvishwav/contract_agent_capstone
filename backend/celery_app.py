"""
Celery application (closes the README's "Async Batch Workflows" future-
enhancement item) for genuinely long-running, multi-LLM-call operations -
currently just contract analysis (see backend/tasks.py for the one task
and why it's the only operation moved here).

Redis is both broker and result backend, reusing the same REDIS_URL
already deployed for caching (P3 item 17/docker-compose's redis service) -
no new infra dependency beyond the Celery package itself.
"""

import os

from celery import Celery

REDIS_URL = os.getenv("REDIS_URL", "redis://localhost:6379")
_EAGER = os.getenv("CELERY_TASK_ALWAYS_EAGER", "false").lower() == "true"

celery_app = Celery(
    "contract_intelligence",
    broker=REDIS_URL,
    # In eager/test mode, use Celery's built-in in-memory result backend
    # instead of real Redis - same rationale as backend/tests/conftest.py
    # forcing RedisCache onto its InMemoryCache fallback: a test run
    # shouldn't depend on whatever Redis happens to be reachable on
    # localhost:6379 (which may need auth, may not exist, or may be an
    # unrelated Redis instance entirely). AsyncResult lookups (task-status
    # polling) still go through a real backend class either way - just an
    # in-memory one for tests, matching this codebase's overall pattern of
    # test determinism over environmental luck.
    backend="cache+memory://" if _EAGER else REDIS_URL,
)

celery_app.conf.update(
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # So a task actually running reports STARTED, not just PENDING then
    # SUCCESS/FAILURE with nothing in between - matters for an operation
    # that can take 20s+.
    task_track_started=True,
    # Long enough to poll a slow analysis to completion and a bit after,
    # not unbounded growth in Redis.
    result_expires=86400,
    # Tests set CELERY_TASK_ALWAYS_EAGER=true (backend/tests/conftest.py) -
    # tasks execute synchronously in-process, no broker/worker needed.
    task_always_eager=_EAGER,
    # Deliberately NOT task_eager_propagates=True: a real (non-eager)
    # .delay() call, against a real worker, never raises the task's own
    # exception synchronously to whoever enqueued it - the whole point of
    # a task queue is that the caller gets a task_id back immediately and
    # discovers failure later via polling. task_eager_propagates=True
    # would make eager/test-mode .delay() behave differently from real
    # production .delay() (raising immediately instead of capturing the
    # exception into a FAILURE-state result), which is exactly backwards
    # for a test suite that exists to catch behavior differences like this.
    task_eager_propagates=False,
    # Reliability/observability audit finding #8: without this, Celery's
    # default is to ack a task the moment the worker *receives* it, before
    # it runs - if the worker process is killed mid-analysis (deploy,
    # OOM, crash), the task is just gone: no retry, no FAILURE state,
    # nothing in the result backend. task_acks_late=True moves the ack to
    # after the task finishes (success or failure), so a killed task goes
    # back on the queue for another worker to pick up instead of vanishing
    # silently - the same "don't mask failure" discipline as this task's
    # own body (see tasks.py's docstring) extended to the process-kill
    # case that discipline can't catch on its own.
    task_acks_late=True,
    # Companion setting: without this, a task whose worker process is
    # killed mid-run (not a task-code exception, an actual SIGKILL/OOM) is
    # requeued forever by default even if it's the *task* crashing the
    # worker every time - reject instead of endless requeue-and-recrash.
    task_reject_on_worker_lost=True,
    # Eager tasks don't persist results to the backend by default (Celery
    # assumes eager execution means the caller already has the return
    # value directly) - without this, a *separate* AsyncResult(task_id)
    # lookup (exactly what GET /tasks/{task_id}/status does, simulating a
    # real second HTTP request polling for a result) finds nothing.
    task_store_eager_result=True,
)

# Import task modules so they register with this app. Done at the bottom,
# after `celery_app` exists, since tasks.py imports it back
# (backend.celery_app.celery_app) - the standard Celery app/tasks split.
from backend import tasks  # noqa: E402,F401

# Prometheus-visible task-state counts (audit finding #10). Signal
# handlers run inside whichever process actually executes the task (the
# `worker` container in real deployments, this same process in eager/test
# mode) - each just records into Redis via celery_task_metrics, which is
# what /api/monitoring/metrics reads back on the `backend` container.
from celery.signals import task_failure, task_prerun, task_retry, task_success  # noqa: E402
from backend.shared.monitoring.celery_task_metrics import record_task_state  # noqa: E402


@task_prerun.connect
def _record_task_prerun(sender=None, **kwargs):
    if sender is not None:
        record_task_state(sender.name, "started")


@task_success.connect
def _record_task_success(sender=None, **kwargs):
    if sender is not None:
        record_task_state(sender.name, "success")


@task_failure.connect
def _record_task_failure(sender=None, **kwargs):
    if sender is not None:
        record_task_state(sender.name, "failure")


@task_retry.connect
def _record_task_retry(sender=None, **kwargs):
    if sender is not None:
        record_task_state(sender.name, "retry")
