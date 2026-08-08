import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from backend.governance.auth import TokenIdentity


class DocumentListTenantScopingTests(unittest.IsolatedAsyncioTestCase):
    async def test_list_uses_authenticated_tenant_inside_the_match(self):
        from backend.api.document_upload import list_uploaded_contracts

        graph = MagicMock()
        graph.query.return_value = [{
            "contract_id": "CONTRACT_A",
            "filename": "Clean_SOW.pdf",
            "upload_date": None,
            "model_used": "gemini-2.5-flash",
            "intelligence_status": None,
            "risk_score": None,
            "risk_level": None,
        }]
        repository = SimpleNamespace(graph=graph)

        with patch(
            "backend.infrastructure.contract_repository.Neo4jContractRepository",
            return_value=repository,
        ):
            result = await list_uploaded_contracts(
                identity=TokenIdentity(tenant_id="tenant-a", role="ADMIN")
            )

        cypher, params = graph.query.call_args.args
        self.assertIn("Contract {tenant_id: $tenant_id}", cypher)
        self.assertEqual(params, {"tenant_id": "tenant-a"})
        self.assertEqual(result[0]["contract_id"], "CONTRACT_A")


if __name__ == "__main__":
    unittest.main()
