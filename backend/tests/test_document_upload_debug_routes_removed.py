"""
Regression test for a real, live cross-tenant data leak found in tonight's
audit (docs/CAPSTONE_SUMMARY.md): document_upload.py's router (registered
unconditionally in main.py, no environment gate) carried its own copy of
GET /debug/contracts and /debug/contract-types with zero tenant_id
filtering - the exact bug production-readiness audit finding #3 already
fixed, but only in the OTHER debug router (api/routes/debug.py, which is
tenant-scoped and fails closed - see test_debug_routes_tenant_isolation.py).

This duplicate was reachable in every environment including production,
by any authenticated caller holding VIEW_AUDIT or VIEW_REPORTS in ANY
tenant. Fix: removed outright rather than patched, since a correct,
tested, fail-closed implementation of the identical functionality already
exists in api/routes/debug.py - maintaining two copies is exactly how this
kind of bug survives a fix applied to only one of them.

This test proves the routes are actually gone from document_upload.py's
router (not just that a query happens to be scoped now), so a future
re-introduction of either path on this router fails immediately.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.api.document_upload import router


class DocumentUploadDebugRoutesRemovedTests(unittest.TestCase):
    def test_debug_contracts_path_does_not_exist_on_this_router(self):
        paths = {r.path for r in router.routes}
        self.assertNotIn("/api/documents/debug/contracts", paths)

    def test_debug_contract_types_path_does_not_exist_on_this_router(self):
        paths = {r.path for r in router.routes}
        self.assertNotIn("/api/documents/debug/contract-types", paths)

    def test_no_route_on_this_router_has_debug_in_its_path_at_all(self):
        """Broader guard than the two exact-path checks above - catches
        any future re-introduction under a slightly different path on
        this same router, not just an exact string match."""
        paths = {r.path for r in router.routes}
        debug_paths = [p for p in paths if "debug" in p.lower()]
        self.assertEqual(debug_paths, [], f"document_upload.py's router must carry no debug routes at all - found {debug_paths}")


if __name__ == "__main__":
    unittest.main()
