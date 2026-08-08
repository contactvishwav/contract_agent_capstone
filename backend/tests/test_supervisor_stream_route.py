"""
Tests for GET /api/supervisor/workflow/{contract_id}/stream - the real
SSE subscriber for the Redis pub/sub progress channel
(agents/supervisor/progress_publisher.py). Uses a fake PubSub object that
returns pre-scripted messages, proving the generator actually reacts to
what a real subscription would deliver rather than following a fixed/
simulated sequence.
"""

import asyncio
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

with patch("langchain_neo4j.Neo4jGraph"), \
     patch("backend.shared.utils.gemini_embedding_service.embedding"):
    import backend.api.supervisor_api as supervisor_api


class FakePubSub:
    def __init__(self, messages):
        self._messages = list(messages)
        self.closed = False

    def subscribe(self, *args, **kwargs):
        pass

    def get_message(self, timeout=None, **kwargs):
        if self._messages:
            return self._messages.pop(0)
        return None

    def close(self):
        self.closed = True


def _msg(step_type, status, **extra):
    return {"type": "message", "data": json.dumps({"step_type": step_type, "status": status, **extra})}


class StreamProgressGeneratorTests(unittest.TestCase):
    def test_yields_each_real_message_and_stops_on_workflow_complete(self):
        pubsub = FakePubSub([
            _msg("workflow", "started"),
            _msg("extract_clauses", "success"),
            _msg("check_policies", "success"),
            _msg("workflow", "complete"),
        ])
        with patch("backend.agents.supervisor.progress_publisher.subscribe", return_value=pubsub):
            events = list(supervisor_api._stream_progress("c1", "tenant_a"))

        self.assertEqual(len(events), 4)
        self.assertIn('"step_type": "workflow"', events[0])
        self.assertIn('"status": "started"', events[0])
        self.assertIn('"status": "complete"', events[-1])
        self.assertTrue(pubsub.closed, "the pubsub connection must be closed when the stream ends")

    def test_stops_on_workflow_failed_too(self):
        pubsub = FakePubSub([
            _msg("workflow", "started"),
            _msg("extract_clauses", "failed"),
            _msg("workflow", "failed", error="boom"),
        ])
        with patch("backend.agents.supervisor.progress_publisher.subscribe", return_value=pubsub):
            events = list(supervisor_api._stream_progress("c1", "tenant_a"))

        self.assertEqual(len(events), 3)
        self.assertIn('"status": "failed"', events[-1])

    def test_non_message_events_are_ignored_not_yielded(self):
        pubsub = FakePubSub([
            {"type": "subscribe", "data": 1},  # subscription confirmation, not a real message
            _msg("workflow", "started"),
            _msg("workflow", "complete"),
        ])
        with patch("backend.agents.supervisor.progress_publisher.subscribe", return_value=pubsub):
            events = list(supervisor_api._stream_progress("c1", "tenant_a"))

        # Only the two real "message"-type events, the subscribe confirmation is skipped.
        self.assertEqual(len(events), 2)

    def test_no_messages_at_all_eventually_times_out(self):
        pubsub = FakePubSub([])  # get_message always returns None -> keepalives forever
        with patch("backend.agents.supervisor.progress_publisher.subscribe", return_value=pubsub), \
             patch.object(supervisor_api, "_STREAM_MAX_SECONDS", 0.05), \
             patch.object(supervisor_api, "_POLL_TIMEOUT_SECONDS", 0.01):
            events = list(supervisor_api._stream_progress("c1", "tenant_a"))

        self.assertTrue(any("stream_timeout" in e for e in events))
        self.assertTrue(pubsub.closed)


class StreamRouteAuthAndTenantScopingTests(unittest.IsolatedAsyncioTestCase):
    async def test_404s_when_contract_does_not_belong_to_the_callers_tenant(self):
        with patch.object(supervisor_api._repository, "get_contract_by_id", new=AsyncMock(return_value=None)):
            from backend.governance.auth import TokenIdentity
            identity = TokenIdentity(tenant_id="tenant_a", role="admin")

            from fastapi import HTTPException
            with self.assertRaises(HTTPException) as ctx:
                await supervisor_api.stream_workflow_progress("c1", identity=identity)

        self.assertEqual(ctx.exception.status_code, 404)

    async def test_returns_a_streaming_response_for_a_contract_the_caller_owns(self):
        with patch.object(supervisor_api._repository, "get_contract_by_id", new=AsyncMock(return_value={"file_id": "c1"})), \
             patch("backend.agents.supervisor.progress_publisher.subscribe", return_value=FakePubSub([])):
            from backend.governance.auth import TokenIdentity
            identity = TokenIdentity(tenant_id="tenant_a", role="admin")

            from fastapi.responses import StreamingResponse
            response = await supervisor_api.stream_workflow_progress("c1", identity=identity)

        self.assertIsInstance(response, StreamingResponse)
        self.assertEqual(response.media_type, "text/event-stream")


if __name__ == "__main__":
    unittest.main()
