import asyncio
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

with patch("langchain_neo4j.Neo4jGraph"), patch(
    "backend.shared.utils.gemini_embedding_service.embedding"
):
    from backend.api import document_upload

from backend.tests.conftest import auth_headers


app = FastAPI()
app.include_router(document_upload.router)
client = TestClient(app)


def repo_with(*query_results):
    repo = MagicMock()
    repo.graph.query.side_effect = list(query_results)
    return repo


def archive_with(repo, role="ADMIN", tenant_id="tenant_a"):
    with patch(
        "backend.infrastructure.contract_repository.Neo4jContractRepository",
        return_value=repo,
    ), patch(
        "backend.infrastructure.audit_logger.AuditLogger.log_event"
    ) as audit, patch(
        "backend.shared.cache.redis_cache.cache.invalidate_tenant_search",
        return_value=4,
    ) as invalidate:
        response = client.delete(
            "/api/documents/C1",
            headers=auth_headers(tenant_id=tenant_id, role=role),
        )
    return response, audit, invalidate


def test_admin_archives_contract_documents_and_specific_sessions_without_physical_delete():
    repo = repo_with(
        [{"filename": "wrong.pdf", "intelligence_status": "completed", "task_state": "SUCCESS"}],
        [{"contract_id": "C1"}],
    )
    response, audit, invalidate = archive_with(repo)

    assert response.status_code == 200
    assert response.json()["physical_data_deleted"] is False
    assert response.json()["sessions"] == "archived_and_hidden"
    archive_query, params = repo.graph.query.call_args_list[1].args
    assert "lifecycle_status = 'ARCHIVED'" in archive_query
    assert "session.archived_at" in archive_query
    assert "tenant_id: $tenant_id" in archive_query
    assert params["tenant_id"] == "tenant_a"
    invalidate.assert_called_once_with("tenant_a")
    assert audit.call_args.kwargs["action"] == "contract_archived"


def test_non_delete_role_is_forbidden_before_repository_access():
    repo = repo_with()
    response, _, _ = archive_with(repo, role="LEGAL_REVIEWER")
    assert response.status_code == 403
    repo.graph.query.assert_not_called()


def test_cross_tenant_missing_and_already_archived_are_same_404():
    repo = repo_with([])
    response, _, _ = archive_with(repo, tenant_id="tenant_b")
    assert response.status_code == 404
    query, params = repo.graph.query.call_args.args
    assert params == {"contract_id": "C1", "tenant_id": "tenant_b"}
    assert "lifecycle_status" in query


def test_running_analysis_blocks_archive():
    repo = repo_with([{"filename": "busy.pdf", "intelligence_status": "processing", "task_state": "STARTED"}])
    response, _, invalidate = archive_with(repo)
    assert response.status_code == 409
    assert "analysis is running" in response.json()["detail"]
    invalidate.assert_not_called()


def test_contract_lists_and_chat_sessions_exclude_archived_records():
    contract_repo = MagicMock()
    contract_repo.graph.query.return_value = []
    with patch(
        "backend.infrastructure.contract_repository.Neo4jContractRepository",
        return_value=contract_repo,
    ):
        asyncio.run(document_upload.list_uploaded_contracts(SimpleNamespace(tenant_id="tenant_a")))
    list_query, list_params = contract_repo.graph.query.call_args.args
    assert "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'" in list_query
    assert list_params == {"tenant_id": "tenant_a"}

    from backend.infrastructure.chat_session_repository import Neo4jChatSessionRepository

    graph = MagicMock()
    graph.query.return_value = []
    sessions = Neo4jChatSessionRepository()
    sessions.graph = graph
    sessions.list_sessions("tenant_a")
    sessions.get_session("S1", "tenant_a")
    assert all("archived_at IS NULL" in call.args[0] for call in graph.query.call_args_list)
