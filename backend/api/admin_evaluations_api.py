"""Read-only surface for backend/scripts/evaluate_retrieval.py's Golden
Dataset Recall@K/nDCG@K results (Phase 5, MLOps governance harness).

This is a system-wide quality metric, not tenant-owned data - gated on
identity (ADMIN role, same reasoning as Phase 4's human-review endpoints:
requires_role, not a Permission any other role could later be granted) rather
than a tenant predicate. The endpoint never runs the evaluation itself
(a real Neo4j vector search + tenant bootstrap per query is a batch job, not
request-time work) - it only serves the latest artifact the script wrote.
"""

import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends

from backend.governance.auth import TokenIdentity
from backend.governance.rbac import UserRole, requires_role
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

router = APIRouter(prefix="/api/admin", tags=["admin"])

RESULTS_PATH = Path(__file__).resolve().parents[1] / "tests" / "evals" / "latest_results.json"


@router.get("/evaluations")
async def get_latest_evaluation(
    _identity: TokenIdentity = Depends(requires_role(UserRole.ADMIN)),
) -> dict[str, Any]:
    if not RESULTS_PATH.exists():
        return {
            "available": False,
            "message": "No evaluation has been run yet. Run backend/scripts/evaluate_retrieval.py.",
        }

    try:
        results = json.loads(RESULTS_PATH.read_text())
    except (OSError, json.JSONDecodeError) as exc:
        logger.error(f"Failed to read evaluation results: {exc}")
        return {
            "available": False,
            "message": "Latest evaluation results could not be read.",
        }

    return {"available": True, **results}
