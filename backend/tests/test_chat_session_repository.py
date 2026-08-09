"""
Neo4jChatSessionRepository unit tests, against an in-memory fake graph that
only understands this repository's exact Cypher shapes (same style as
test_policy_repository_tenant_isolation.py's FakePolicyGraph) - session
creation, listing (unfiltered vs. per-contract), ownership-checked get,
message append/ordering, and the encryption-at-rest round trip.
"""

import unittest
from unittest.mock import patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository


class _FakeNeo4jDateTime(int):
    """Orderable (so ordering-by-updated_at tests still work) stand-in for
    neo4j.time.DateTime - real Cypher datetime() values expose .iso_format(),
    which backend/shared/utils/utils.py's serialize_neo4j_datetime() (used
    by backend/api/chat_sessions.py) checks for via hasattr(). A plain int
    would fail SessionResponse's Pydantic validation the same way a real
    unconverted neo4j.time.DateTime would."""

    def iso_format(self):
        return f"2026-01-01T00:00:{int(self):02d}Z"


class FakeChatSessionGraph:
    """In-memory stand-in for Neo4jGraph understanding
    Neo4jChatSessionRepository's exact Cypher shapes. Mirrors real Neo4j
    MATCH semantics: a session is only found when session_id AND tenant_id
    both agree with what's stored."""

    def __init__(self):
        self.sessions = {}   # session_id -> dict
        self.messages = {}   # session_id -> list[dict], insertion order
        self._clock = 0

    def _lookup_session(self, params):
        session = self.sessions.get(params.get("session_id"))
        if not session:
            return None
        if session["tenant_id"] != params.get("tenant_id"):
            return None
        return session

    def query(self, cypher, params=None):
        params = params or {}

        if "CREATE (s:ChatSession" in cypher:
            self._clock += 1
            session = {
                "session_id": params["session_id"], "tenant_id": params["tenant_id"],
                "contract_id": params.get("contract_id"), "title": params["title"],
                "created_at": _FakeNeo4jDateTime(self._clock), "updated_at": _FakeNeo4jDateTime(self._clock),
                "message_count": 0,
            }
            self.sessions[params["session_id"]] = session
            self.messages[params["session_id"]] = []
            return [dict(session)]

        if "SET s.message_count" in cypher:
            session = self._lookup_session(params)
            if not session:
                return []
            session["message_count"] += 1
            self._clock += 1
            session["updated_at"] = _FakeNeo4jDateTime(self._clock)
            seq = session["message_count"]
            message = {
                "message_id": params["message_id"], "role": params["role"], "content": params["content"],
                "model": params.get("model"), "tool_name": params.get("tool_name"),
                "tool_call_id": params.get("tool_call_id"), "citations": params.get("citations"),
                "terminal_status": params.get("terminal_status"),
                "terminal_reason": params.get("terminal_reason"),
                "created_at": _FakeNeo4jDateTime(self._clock), "sequence": seq,
            }
            self.messages[params["session_id"]].append(message)
            return [{"message_id": message["message_id"], "sequence": seq, "created_at": message["created_at"]}]

        if "HAS_MESSAGE" in cypher and "RETURN m." in cypher:
            session = self._lookup_session(params)
            if not session:
                return []
            return [dict(m) for m in sorted(self.messages[params["session_id"]], key=lambda m: m["sequence"])]

        if "ORDER BY s.updated_at DESC" in cypher:
            rows = [s for s in self.sessions.values() if s["tenant_id"] == params.get("tenant_id")]
            if "contract_id" in params:
                rows = [s for s in rows if s.get("contract_id") == params["contract_id"]]
            rows.sort(key=lambda s: s["updated_at"], reverse=True)
            return [dict(r) for r in rows]

        if "SET s.title" in cypher:
            session = self._lookup_session(params)
            if not session:
                return []
            self._clock += 1
            session["title"] = params["title"]
            session["updated_at"] = _FakeNeo4jDateTime(self._clock)
            return [dict(session)]

        if "RETURN s.session_id" in cypher:  # get_session (no ORDER BY)
            session = self._lookup_session(params)
            return [dict(session)] if session else []

        return []


def make_repo():
    graph = FakeChatSessionGraph()
    repo = Neo4jChatSessionRepository()
    repo.graph = graph
    return repo, graph


class CreateSessionTests(unittest.TestCase):
    def test_creates_contract_scoped_session(self):
        repo, graph = make_repo()
        row = repo.create_session("tenant_a", "CONTRACT_1", "First chat")
        self.assertTrue(row["session_id"].startswith("SESSION_"))
        self.assertEqual(row["contract_id"], "CONTRACT_1")
        self.assertEqual(row["message_count"], 0)

    def test_creates_all_contracts_session_with_omitted_contract_id(self):
        """contract_id=None must be OMITTED, not stored as a null property -
        list_sessions relies on this (see below)."""
        repo, graph = make_repo()
        row = repo.create_session("tenant_a", None, "General questions")
        self.assertIsNone(row["contract_id"])


class ListSessionsTests(unittest.TestCase):
    def test_no_filter_returns_every_session_for_tenant(self):
        repo, graph = make_repo()
        repo.create_session("tenant_a", "CONTRACT_1", "Chat 1")
        repo.create_session("tenant_a", None, "Chat 2")
        repo.create_session("tenant_b", "CONTRACT_1", "Other tenant's chat")

        rows = repo.list_sessions("tenant_a")
        self.assertEqual(len(rows), 2)

    def test_contract_filter_returns_only_that_contracts_sessions(self):
        repo, graph = make_repo()
        repo.create_session("tenant_a", "CONTRACT_1", "Chat 1")
        repo.create_session("tenant_a", "CONTRACT_2", "Chat 2")

        rows = repo.list_sessions("tenant_a", contract_id="CONTRACT_1")
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["contract_id"], "CONTRACT_1")

    def test_sorted_most_recently_updated_first(self):
        repo, graph = make_repo()
        first = repo.create_session("tenant_a", None, "Older")
        second = repo.create_session("tenant_a", None, "Newer")
        repo.append_message(first["session_id"], "tenant_a", role="user_message", content="bump the older one")

        rows = repo.list_sessions("tenant_a")
        self.assertEqual(rows[0]["session_id"], first["session_id"], "the just-bumped session must sort first")
        self.assertEqual(rows[1]["session_id"], second["session_id"])


class GetSessionTests(unittest.TestCase):
    def test_returns_session_for_owning_tenant(self):
        repo, graph = make_repo()
        created = repo.create_session("tenant_a", "CONTRACT_1", "Chat")
        fetched = repo.get_session(created["session_id"], "tenant_a")
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["session_id"], created["session_id"])

    def test_returns_none_for_wrong_tenant(self):
        repo, graph = make_repo()
        created = repo.create_session("tenant_a", "CONTRACT_1", "Chat")
        self.assertIsNone(repo.get_session(created["session_id"], "tenant_b"))

    def test_returns_none_for_unknown_session_id(self):
        repo, graph = make_repo()
        self.assertIsNone(repo.get_session("SESSION_DOES_NOT_EXIST", "tenant_a"))


class RenameSessionTests(unittest.TestCase):
    def test_rename_is_tenant_scoped_and_updates_title(self):
        repo, _ = make_repo()
        created = repo.create_session("tenant_a", None, "Original")
        self.assertIsNone(repo.rename_session(created["session_id"], "tenant_b", "Intruder"))
        renamed = repo.rename_session(created["session_id"], "tenant_a", "Useful name")
        self.assertEqual(renamed["title"], "Useful name")


class AppendMessageAndListMessagesTests(unittest.TestCase):
    def test_sequential_messages_get_gapless_increasing_sequence(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]

        r1 = repo.append_message(sid, "tenant_a", role="user_message", content="hi")
        r2 = repo.append_message(sid, "tenant_a", role="ai_message", content="hello")
        r3 = repo.append_message(sid, "tenant_a", role="tool_call", content="{}", tool_name="Search")

        self.assertEqual([r1["sequence"], r2["sequence"], r3["sequence"]], [1, 2, 3])

    def test_message_count_and_updated_at_bump_on_append(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        before_updated = graph.sessions[sid]["updated_at"]

        repo.append_message(sid, "tenant_a", role="user_message", content="hi")

        self.assertEqual(graph.sessions[sid]["message_count"], 1)
        self.assertGreater(graph.sessions[sid]["updated_at"], before_updated)

    def test_append_to_wrong_tenants_session_is_a_no_op(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]

        result = repo.append_message(sid, "tenant_b", role="user_message", content="attacker message")

        self.assertIsNone(result)
        self.assertEqual(graph.sessions[sid]["message_count"], 0)
        self.assertEqual(repo.list_messages(sid, "tenant_a"), [])

    def test_list_messages_returns_in_insertion_order(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        repo.append_message(sid, "tenant_a", role="user_message", content="first")
        repo.append_message(sid, "tenant_a", role="ai_message", content="second")
        repo.append_message(sid, "tenant_a", role="user_message", content="third")

        messages = repo.list_messages(sid, "tenant_a")
        self.assertEqual([m["content"] for m in messages], ["first", "second", "third"])
        self.assertEqual([m["sequence"] for m in messages], [1, 2, 3])

    def test_assistant_terminal_status_round_trips_without_changing_role(self):
        repo, _ = make_repo()
        session = repo.create_session("tenant_a", None, "Guard failure")

        repo.append_message(
            session["session_id"],
            "tenant_a",
            role="ai_message",
            content="Response validation failed. Please retry.",
            model="gemini-2.5-flash",
            terminal_status="validation_failed",
            terminal_reason="infrastructure",
        )

        message = repo.list_messages(session["session_id"], "tenant_a")[0]
        self.assertEqual(message["role"], "ai_message")
        self.assertEqual(message["terminal_status"], "validation_failed")
        self.assertEqual(message["terminal_reason"], "infrastructure")

    def test_content_is_encrypted_at_rest_and_decrypted_on_read(self):
        """Same assertion style as test_chunk_encryption_at_rest.py: the
        fake graph (standing in for real Neo4j) must never see plaintext,
        while the repository's own read path returns plaintext."""
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        sid = session["session_id"]
        plaintext = "The total project fee is $500,000."

        repo.append_message(sid, "tenant_a", role="ai_message", content=plaintext)

        stored_ciphertext = graph.messages[sid][0]["content"]
        self.assertNotEqual(stored_ciphertext, plaintext, "content must not be stored in plaintext")

        decrypted = repo.list_messages(sid, "tenant_a")[0]["content"]
        self.assertEqual(decrypted, plaintext)

    def test_citations_are_encrypted_at_rest_and_restored(self):
        repo, graph = make_repo()
        session = repo.create_session("tenant_a", None, "Chat")
        citations = [{"citation_id": "CIT_1", "contract_id": "CONTRACT_1"}]

        repo.append_message(session["session_id"], "tenant_a", role="ai_message", content="Answer", citations=citations)

        self.assertNotIn("CONTRACT_1", graph.messages[session["session_id"]][0]["citations"])
        self.assertEqual(repo.list_messages(session["session_id"], "tenant_a")[0]["citations"], citations)


if __name__ == "__main__":
    unittest.main()
