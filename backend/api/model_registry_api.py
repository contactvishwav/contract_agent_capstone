from typing import Literal

from fastapi import APIRouter, Depends, Query

from backend.governance.auth import TokenIdentity, get_current_identity
from backend.model_registry import DEFAULT_MODEL, FIXED_EMBEDDING, available_models


router = APIRouter(prefix="/api/models", tags=["models"])


@router.get("")
async def get_models(
    workflow: Literal["chat", "analysis", "upload"] = Query("chat"),
    _identity: TokenIdentity = Depends(get_current_identity),
):
    models = available_models(workflow)
    ids = [spec.stable_id for spec in models]
    return {
        "workflow": workflow,
        "models": [spec.public() for spec in models],
        "default_model": DEFAULT_MODEL if DEFAULT_MODEL in ids else (ids[0] if ids else None),
        "embedding": FIXED_EMBEDDING,
        "fallback_policy": {
            "automatic_cross_provider": False,
            "disclosure_required": True,
            "legal_analysis": "fail_explicitly",
        },
    }
