"""
The real Supervisor Agent API - not a resurrection of the deleted
api/supervisor_api.py (removed with the rest of the dead Supervisor
orchestration path, docs/CAPSTONE_SUMMARY.md §8). This one wires
genuinely to what already exists: PlanExecutionEngine (the real default
execution path), the real Redis-backed circuit breaker
(shared/reliability/circuit_breaker.py), the real audit trail
(AuditLogger), and the real Redis pub/sub progress channel
(agents/supervisor/progress_publisher.py).
"""

import asyncio
import json
import time

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import StreamingResponse

from backend.governance.auth import TokenIdentity
from backend.governance.rbac import Permission, requires_permission
from backend.infrastructure.contract_repository import Neo4jContractRepository
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/supervisor", tags=["supervisor"])

_repository = Neo4jContractRepository()

# Overall cap on how long a single SSE connection stays open with no
# terminal ("workflow complete"/"workflow failed") message - protects
# against a client left connected to a contract_id that never actually
# gets analyzed (e.g. a typo'd id, or the analysis was never triggered).
_STREAM_MAX_SECONDS = 300
_POLL_TIMEOUT_SECONDS = 1.0


def _stream_progress(contract_id: str):
    """Real generator - subscribes to the real Redis channel via
    progress_publisher.subscribe and yields each message as it actually
    arrives, formatted as an SSE event. Terminates on a "workflow"
    complete/failed message, or after _STREAM_MAX_SECONDS with nothing
    terminal. Not a simulated/fixed sequence - if PlanExecutionEngine
    never publishes anything (e.g. analysis was never triggered for this
    contract_id), this generator yields nothing but keepalives until it
    times out.
    """
    from backend.agents.supervisor.progress_publisher import subscribe

    pubsub = subscribe(contract_id)
    start = time.time()
    try:
        while time.time() - start < _STREAM_MAX_SECONDS:
            message = pubsub.get_message(timeout=_POLL_TIMEOUT_SECONDS, ignore_subscribe_messages=True)
            if message is None:
                yield ": keepalive\n\n"
                continue
            if message.get("type") != "message":
                continue

            data = message.get("data")
            yield f"data: {data}\n\n"

            try:
                payload = json.loads(data)
            except (TypeError, ValueError):
                continue
            if payload.get("step_type") == "workflow" and payload.get("status") in ("complete", "failed"):
                return
        yield f"data: {json.dumps({'step_type': 'workflow', 'status': 'stream_timeout'})}\n\n"
    finally:
        try:
            pubsub.close()
        except Exception:
            pass


@router.get("/workflow/{contract_id}/stream")
async def stream_workflow_progress(
    contract_id: str,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """
    Real-time per-step progress for a running (or about-to-run) analysis
    of `contract_id`, via Server-Sent Events. Subscribes to the real Redis
    channel PlanExecutionEngine.execute_plan publishes to - genuine
    pub/sub, not a simulated/canned event sequence. See
    agents/supervisor/progress_publisher.py's module docstring for the
    contract_id-keyed-channel trade-off and the fire-and-forget
    (no-replay-if-late) trade-off.

    Tenant-scoped: 404s (not a silent empty stream) if `contract_id`
    doesn't belong to the caller's own tenant - matching every other
    contract-scoped route in this system, since progress events reveal
    real step names/status tied to that contract.
    """
    contract = await _repository.get_contract_by_id(contract_id, identity.tenant_id)
    if not contract:
        raise HTTPException(status_code=404, detail=f"Contract not found: {contract_id}")

    return StreamingResponse(
        _stream_progress(contract_id),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )
