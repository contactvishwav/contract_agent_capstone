from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.governance.auth import TokenIdentity, get_current_identity
from backend.model_registry import DEFAULT_MODEL, FIXED_EMBEDDING, available_models
from backend.routing_service import AUTO_MODEL_ID, STUDENT_MODEL_ID, TEACHER_MODEL_ID


router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def get_models(
    workflow: Literal["chat", "analysis", "upload"] = Query("chat"),
    _identity: TokenIdentity = Depends(get_current_identity),
):
    models = available_models(workflow)
    ids = [spec.stable_id for spec in models]
    response_models = [spec.public() for spec in models]

    # Phase 6: only offer autonomous routing when both of its candidate
    # models are actually available for this workflow - otherwise "Auto"
    # would be selectable and then fail at request time for a reason the
    # user can't see from the dropdown.
    if workflow == "chat" and STUDENT_MODEL_ID in ids and TEACHER_MODEL_ID in ids:
        response_models.insert(0, {
            "id": AUTO_MODEL_ID,
            "provider": "router",
            "display_label": "Autonomous Routing (Student ⇄ Teacher)",
            "configured": True,
            "capabilities": sorted({"chat", "tool_calling", "streaming", "vision"}),
            "production_allowed": True,
            "fallback_eligible": False,
            "cost_class": "auto",
            "latency_class": "auto",
            "deprecated": False,
        })

    return {
        "workflow": workflow,
        "models": response_models,
        "default_model": DEFAULT_MODEL if DEFAULT_MODEL in ids else (ids[0] if ids else None),
        "embedding": FIXED_EMBEDDING,
        "fallback_policy": {
            "automatic_cross_provider": False,
            "disclosure_required": True,
            "legal_analysis": "fail_explicitly",
        },
    }
