"""
Regression tests for P3 item 21: PII detection was chatbot-only (never
covered ingestion), and stored contract text/clause content had no
encryption at rest.

Covers:
- SecurityValidator's is_valid=True bug (a PII hit was always invisible to
  ContentValidationService's has_warnings/has_errors aggregation) - now
  fixed, and reusing PIIEngine (the same detection logic the chat path
  already uses) instead of a second, independently reimplemented pattern
  set.
- Neo4jContractRepository.store_contract / ClauseRepository._store_single_
  clause now redact PII (PIIEngine.redact()) and encrypt (FieldEncryptor,
  AES-256-GCM) full_text/content before persistence - and decrypt on read.
- Contract.contains_pii, populated at ingestion.
- FieldEncryptor/EnvKeyProvider round-trip correctness.
"""

import ast
import os
import unittest
from unittest.mock import MagicMock, patch

from backend.governance.pii_engine import PIIEngine
from backend.infrastructure.content_validator import ContentValidationService, SecurityValidator
from backend.infrastructure.encryption import EnvKeyProvider, FieldEncryptor

BACKEND_DIR = os.path.join(os.path.dirname(__file__), "..")

SSN_TEXT = "Employee SSN on file: 123-45-6789. Rest of the contract text follows normally."
NO_PII_TEXT = "This is a normal contract clause with no personal information in it at all."


class SecurityValidatorTests(unittest.TestCase):
    def test_pii_hit_is_invalid_with_warning_severity(self):
        results = SecurityValidator().validate({"full_text": SSN_TEXT})
        pii_results = [r for r in results if r.details.get("pii_type") == "SSN"]
        self.assertEqual(len(pii_results), 1)
        self.assertFalse(pii_results[0].is_valid)
        self.assertEqual(pii_results[0].severity.value, "warning")

    def test_no_pii_produces_a_passing_info_result(self):
        results = SecurityValidator().validate({"full_text": NO_PII_TEXT})
        self.assertTrue(all(r.is_valid for r in results))

    def test_content_validation_service_surfaces_pii_as_warning_not_error(self):
        # The actual regression: previously has_warnings was never True for
        # a PII hit (SecurityValidator's own is_valid=True bug made it
        # invisible to this aggregation), so nothing downstream could ever
        # tell a PII hit had occurred.
        result = ContentValidationService().validate({
            "filename": "contract.pdf",
            "file_size": 1024,
            "full_text": SSN_TEXT * 5,  # long enough to pass ContentQualityValidator's min_length
            "contract_type": "Service Agreement",
            "summary": "test",
            "parties": [{"name": "A", "role": "Provider"}],
        })
        self.assertTrue(result["has_warnings"])
        self.assertFalse(result["has_errors"], "PII must not block ingestion outright - redact, don't block")
        self.assertTrue(result["is_valid"], "warnings alone must not flip the overall is_valid to False")


class FieldEncryptorTests(unittest.TestCase):
    def setUp(self):
        self.encryptor = FieldEncryptor()

    def test_round_trip(self):
        ciphertext = self.encryptor.encrypt(SSN_TEXT)
        self.assertNotEqual(ciphertext, SSN_TEXT)
        self.assertEqual(self.encryptor.decrypt(ciphertext), SSN_TEXT)

    def test_different_plaintexts_produce_different_ciphertexts(self):
        self.assertNotEqual(self.encryptor.encrypt("text one"), self.encryptor.encrypt("text two"))

    def test_same_plaintext_encrypted_twice_produces_different_ciphertext(self):
        # Random nonce per call - proves it's not a naive deterministic cipher.
        self.assertNotEqual(self.encryptor.encrypt(SSN_TEXT), self.encryptor.encrypt(SSN_TEXT))

    def test_empty_string_round_trips_as_empty(self):
        self.assertEqual(self.encryptor.decrypt(self.encryptor.encrypt("")), "")

    def test_decrypt_falls_back_to_input_on_invalid_ciphertext(self):
        # Safety net for legacy plaintext data written before encryption
        # existed - must not crash, must not silently return garbage.
        legacy_plaintext = "this was never encrypted"
        self.assertEqual(self.encryptor.decrypt(legacy_plaintext), legacy_plaintext)

    def test_env_key_provider_falls_back_to_dev_default_when_unset(self):
        with patch.dict(os.environ, {}, clear=False):
            os.environ.pop("ENCRYPTION_KEY", None)
            key = EnvKeyProvider().get_key()
        self.assertEqual(len(key), 32)

    def test_env_key_provider_uses_configured_key_when_set(self):
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "a-real-configured-secret"}):
            key_a = EnvKeyProvider().get_key()
        with patch.dict(os.environ, {"ENCRYPTION_KEY": "a-different-configured-secret"}):
            key_b = EnvKeyProvider().get_key()
        self.assertNotEqual(key_a, key_b)


class FakeGraph:
    """Records every issued (cypher, params). CREATE (c:Contract queries
    return a row so store_contract's `if not result: raise` doesn't fire;
    everything else (party/governing-law relationship writes) returns []."""

    def __init__(self):
        self.queries = []

    def query(self, cypher, params=None):
        params = params or {}
        self.queries.append((cypher, params))
        if "CREATE (c:Contract" in cypher:
            return [{"contract_id": params["file_id"]}]
        return []


def _repository_with_fake_graph():
    with patch("langchain_neo4j.Neo4jGraph"), \
         patch("backend.shared.utils.gemini_embedding_service.embedding"):
        from backend.infrastructure.contract_repository import Neo4jContractRepository
    repo = Neo4jContractRepository()
    fake_graph = FakeGraph()
    repo.graph = fake_graph
    repo.embedding_service = MagicMock(embed_query=MagicMock(return_value=[]))
    return repo, fake_graph


class ContractRepositoryPIIEncryptionTests(unittest.IsolatedAsyncioTestCase):
    async def test_store_contract_redacts_and_encrypts_full_text(self):
        repo, fake_graph = _repository_with_fake_graph()

        await repo.store_contract({"full_text": SSN_TEXT, "summary": "s"}, tenant_id="t1")

        create_calls = [(c, p) for c, p in fake_graph.queries if "CREATE (c:Contract" in c]
        self.assertEqual(len(create_calls), 1)
        stored_full_text = create_calls[0][1]["full_text"]

        # Not the raw plaintext, and not just redacted-but-still-plaintext -
        # genuinely encrypted (unreadable without decrypting).
        self.assertNotEqual(stored_full_text, SSN_TEXT)
        self.assertNotIn("123-45-6789", stored_full_text)
        self.assertNotIn("[REDACTED_SSN]", stored_full_text)

    async def test_store_contract_marks_contains_pii_true_when_pii_present(self):
        repo, fake_graph = _repository_with_fake_graph()
        await repo.store_contract({"full_text": SSN_TEXT, "summary": "s"}, tenant_id="t1")

        create_calls = [(c, p) for c, p in fake_graph.queries if "CREATE (c:Contract" in c]
        self.assertTrue(create_calls[0][1]["contains_pii"])

    async def test_store_contract_marks_contains_pii_false_when_no_pii(self):
        repo, fake_graph = _repository_with_fake_graph()
        await repo.store_contract({"full_text": NO_PII_TEXT, "summary": "s"}, tenant_id="t1")

        create_calls = [(c, p) for c, p in fake_graph.queries if "CREATE (c:Contract" in c]
        self.assertFalse(create_calls[0][1]["contains_pii"])

    async def test_get_contract_by_id_decrypts_full_text_and_returns_contains_pii(self):
        from backend.infrastructure.encryption import field_encryptor

        repo, fake_graph = _repository_with_fake_graph()
        redacted_text = PIIEngine.redact(SSN_TEXT)
        encrypted_text = field_encryptor.encrypt(redacted_text)

        class ReadFakeGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "MATCH (c:Contract {file_id:" in cypher:
                    return [{
                        "file_id": "c1", "contract_type": "Service Agreement", "summary": "s",
                        "contract_scope": "", "full_text": encrypted_text, "contains_pii": True,
                        "effective_date": None, "end_date": None, "total_amount": None,
                        "parties": [{"name": None, "role": None}],
                    }]
                return super().query(cypher, params)

        repo.graph = ReadFakeGraph()
        result = await repo.get_contract_by_id("c1", "t1")

        self.assertEqual(result["full_text"], redacted_text)
        self.assertTrue(result["contains_pii"])


class ClauseRepositoryPIIEncryptionTests(unittest.TestCase):
    def _repository(self):
        with patch("langchain_neo4j.Neo4jGraph"), \
             patch("backend.shared.utils.gemini_embedding_service.embedding"):
            from backend.infrastructure.clause_repository import ClauseRepository
        repo = ClauseRepository()
        fake_graph = FakeGraph()
        repo.repository.graph = fake_graph
        return repo, fake_graph

    def test_store_single_clause_encrypts_content(self):
        repo, fake_graph = self._repository()
        repo._store_single_clause({
            "section_id": "sec1", "clause_id": "cl1", "content": SSN_TEXT,
            "clause_type": "General", "order": 0, "confidence": 0.9,
        })

        create_calls = [(c, p) for c, p in fake_graph.queries if "CREATE (cl:Clause" in c]
        self.assertEqual(len(create_calls), 1)
        stored_content = create_calls[0][1]["content"]
        self.assertNotEqual(stored_content, SSN_TEXT)
        self.assertNotIn("123-45-6789", stored_content)

    def test_get_clauses_ordered_decrypts_content(self):
        from backend.infrastructure.encryption import field_encryptor

        repo, fake_graph = self._repository()
        encrypted_content = field_encryptor.encrypt(SSN_TEXT)

        class ReadFakeGraph(FakeGraph):
            def query(self, cypher, params=None):
                if "CONTAINS_CLAUSE" in cypher and "MATCH (s:Section" in cypher:
                    return [{
                        "clause_id": "cl1", "content": encrypted_content, "clause_type": "General",
                        "order": 0, "confidence": 0.9, "cuad_classifications": [],
                    }]
                return super().query(cypher, params)

        repo.repository.graph = ReadFakeGraph()
        result = repo.get_clauses_ordered("sec1")

        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["content"], SSN_TEXT)


def _upload_route_function(module_path, function_name):
    with open(module_path, encoding="utf-8") as f:
        tree = ast.parse(f.read(), filename=module_path)
    for node in ast.walk(tree):
        if isinstance(node, ast.AsyncFunctionDef) and node.name == function_name:
            return node
    raise AssertionError(f"function {function_name} not found in {module_path}")


def _try_node_in(function_node):
    for node in ast.walk(function_node):
        if isinstance(node, ast.Try):
            return node
    raise AssertionError("no try/except/finally block found")


def _calls_os_remove(nodes):
    for node in nodes:
        for sub in ast.walk(node):
            if (
                isinstance(sub, ast.Call)
                and isinstance(sub.func, ast.Attribute)
                and sub.func.attr == "remove"
            ):
                return True
    return False


class UploadTempFileCleanupRegressionTests(unittest.TestCase):
    """
    Both upload routes only removed the temp-saved PDF (containing raw,
    unredacted contract text) in their except-Exception branch - never on
    a successful upload, so the enable_enhanced=True path left every
    successfully-processed PDF on disk in /tmp indefinitely. Fixed by
    moving cleanup into the unconditional `finally` block. This guards
    against the fix being reverted (moved back into only `except`).
    """

    def test_document_upload_cleans_up_in_finally_block(self):
        node = _upload_route_function(
            os.path.join(BACKEND_DIR, "api", "document_upload.py"), "upload_pdf"
        )
        try_node = _try_node_in(node)
        self.assertTrue(
            _calls_os_remove(try_node.finalbody),
            "temp file cleanup must run in the `finally` block, not only on exception",
        )

    def test_enhanced_document_upload_cleans_up_in_finally_block(self):
        node = _upload_route_function(
            os.path.join(BACKEND_DIR, "api", "enhanced_document_upload.py"), "upload_pdf_enhanced"
        )
        try_node = _try_node_in(node)
        self.assertTrue(
            _calls_os_remove(try_node.finalbody),
            "temp file cleanup must run in the `finally` block, not only on exception",
        )


class EnhancedUploadValidationWiringTests(unittest.TestCase):
    """enhanced_document_upload.py previously had no content/PII validation
    at all, unlike /api/documents/upload."""

    def test_enhanced_upload_invokes_content_validation(self):
        path = os.path.join(BACKEND_DIR, "api", "enhanced_document_upload.py")
        with open(path, encoding="utf-8") as f:
            source = f.read()
        self.assertIn("ContentValidationService", source)
        self.assertIn(".validate(", source)


if __name__ == "__main__":
    unittest.main()
