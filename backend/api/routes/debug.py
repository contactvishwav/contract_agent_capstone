from fastapi import APIRouter, Depends
from backend.governance.rbac import Permission, requires_permission
from backend.governance.auth import TokenIdentity
from backend.infrastructure.contract_repository import Neo4jContractRepository
import os

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)


def _debug_routes_enabled() -> bool:
    """
    Production-readiness audit finding #3: was `os.getenv("ENVIRONMENT",
    "development") != "production"` - unset ENVIRONMENT defaulted to
    "development", which made this fail *open* (debug routes live unless
    someone remembered to set ENVIRONMENT=production). A real deployment
    that simply forgot to set that one var got a live, tenant-unscoped
    contract-listing endpoint.

    Fails closed instead: ENVIRONMENT must be explicitly set to something
    other than "production" to enable these routes at all. Unset (the
    getenv default below is None, not "development") now means hidden,
    matching every other fail-closed default in this pass. Local dev via
    docker-compose sets ENVIRONMENT=development explicitly to opt back in.
    """
    value = os.getenv("ENVIRONMENT")
    return value is not None and value != "production"


def create_debug_router() -> APIRouter:
    """Create debug router only when explicitly enabled for a non-production environment."""
    if not _debug_routes_enabled():
        return APIRouter()

    router = APIRouter(prefix="/debug", tags=["debug"])

    @router.get("/contracts")
    async def list_all_contracts(
        identity: TokenIdentity = Depends(requires_permission(Permission.VIEW_AUDIT)),
    ):
        """Debug endpoint to see this tenant's contracts in the database.

        Tenant-scoped even though this route is dev/staging-only - a
        debug endpoint enabled in a shared staging environment (or
        mistakenly left on) must not become a cross-tenant data leak just
        because it's "only" a debug route. Same real fix regardless of
        why the route exists at all.
        """
        try:
            repo = Neo4jContractRepository()

            query = """
            MATCH (c:Contract {tenant_id: $tenant_id})
            RETURN c.file_id as contract_id,
                   c.contract_type as contract_type,
                   c.summary as summary,
                   c.source as source
            ORDER BY c.upload_date DESC
            """

            result = repo.graph.query(query, {"tenant_id": identity.tenant_id})

            contracts = []
            for row in result:
                contracts.append({
                    "contract_id": row["contract_id"],
                    "contract_type": row["contract_type"],
                    "summary": row["summary"][:100] + "..." if row["summary"] and len(row["summary"]) > 100 else row["summary"],
                    "source": row["source"]
                })

            return {
                "tenant_id": identity.tenant_id,
                "total_contracts": len(contracts),
                "contracts": contracts
            }

        except Exception as e:
            logger.error(f"Debug contracts failed: {e}")
            return {"error": str(e)}

    @router.get("/contract-types")
    async def get_contract_type_counts(
        identity: TokenIdentity = Depends(requires_permission(Permission.VIEW_REPORTS)),
    ):
        """Debug endpoint to see this tenant's contract type distribution."""
        try:
            repo = Neo4jContractRepository()

            query = """
            MATCH (c:Contract {tenant_id: $tenant_id})
            RETURN c.contract_type as contract_type, count(*) as count
            ORDER BY count DESC
            """

            result = repo.graph.query(query, {"tenant_id": identity.tenant_id})

            return {
                "tenant_id": identity.tenant_id,
                "contract_types": [{"type": row["contract_type"], "count": row["count"]} for row in result]
            }

        except Exception as e:
            logger.error(f"Debug contract types failed: {e}")
            return {"error": str(e)}

    return router
