import asyncio
import json
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from fastapi import HTTPException

with patch("langchain_neo4j.Neo4jGraph"), patch(
    "backend.shared.utils.gemini_embedding_service.embedding"
):
    from backend.api import contract_intelligence
    from backend.application.services.contract_intelligence_service import ContractIntelligenceService
    from backend.domain.entities import ContractIntelligence, RiskAssessment
    from backend.infrastructure.encryption import field_encryptor


def identity(tenant_id="tenant_a"):
    return SimpleNamespace(tenant_id=tenant_id)


def analysis_payload(contract_id="C1"):
    return {
        "contract_id": contract_id,
        "analysis_complete": True,
        "model_used": "gemini-2.5-flash",
        "execution_path": "langgraph_traditional_explicit",
        "planned_execution": False,
        "analysis_method": "phase3",
        "node_status": {"extract": "success"},
        "processing_time": 1.2,
        "results": {
            "clauses": [],
            "violations": [],
            "redlines": [],
            "risk_assessment": {
                "overall_risk_score": 10,
                "risk_level": "LOW",
                "critical_issues": [],
                "critical_issue_details": [],
                "recommendations": [],
            },
        },
    }


def test_latest_analysis_is_decrypted_and_tenant_scoped():
    payload = analysis_payload()
    fake_graph = MagicMock()
    fake_graph.query.return_value = [{
        "filename": "msa.pdf",
        "intelligence_status": "completed",
        "analysis_id": "A1",
        "analysis_status": "completed",
        "result_payload": field_encryptor.encrypt(json.dumps(payload)),
        "analysis_created_at": None,
    }]
    with patch.object(contract_intelligence.repository, "graph", fake_graph):
        response = asyncio.run(contract_intelligence.get_latest_contract_analysis("C1", identity()))

    assert response["source"] == "persisted_analysis"
    assert response["analysis"] == payload
    assert fake_graph.query.call_args.args[1] == {"contract_id": "C1", "tenant_id": "tenant_a"}
    assert "lifecycle_status" in fake_graph.query.call_args.args[0]


def test_legacy_completed_analysis_returns_honest_summary_without_detail():
    fake_graph = MagicMock()
    fake_graph.query.return_value = [{
        "filename": "legacy.pdf",
        "intelligence_status": "completed_with_errors",
        "risk_score": 71,
        "risk_level": "HIGH",
        "violations_count": 3,
        "clauses_count": 9,
        "redlines_count": 2,
        "execution_path": "langgraph_traditional_explicit",
        "planned_execution": False,
        "model_used": "gemini-2.5-flash",
        "result_payload": None,
    }]
    with patch.object(contract_intelligence.repository, "graph", fake_graph):
        response = asyncio.run(contract_intelligence.get_latest_contract_analysis("C1", identity()))

    assert response["legacy_summary"] is True
    assert response["state"] == "completed_with_errors"
    assert response["analysis"]["analysis_complete"] is False
    assert response["analysis"]["summary_counts"] == {"clauses": 9, "violations": 3, "redlines": 2}


def test_missing_or_cross_tenant_contract_is_non_disclosing_404():
    fake_graph = MagicMock()
    fake_graph.query.return_value = []
    with patch.object(contract_intelligence.repository, "graph", fake_graph), pytest.raises(HTTPException) as exc:
        asyncio.run(contract_intelligence.get_latest_contract_analysis("OTHER", identity()))
    assert exc.value.status_code == 404
    assert exc.value.detail == "Contract not found"


def test_analysis_store_writes_complete_payload_and_summary_atomically():
    fake_graph = MagicMock()
    fake_graph.query.return_value = [{"contract_id": "C1", "analysis_id": "A1"}]
    service = ContractIntelligenceService.__new__(ContractIntelligenceService)
    service.repository = SimpleNamespace(graph=fake_graph)
    service._store_performance_metrics = MagicMock()
    intelligence = ContractIntelligence(
        clauses=[],
        violations=[],
        risk_assessment=RiskAssessment(12, "LOW", [], []),
        redlines=[],
        execution_path="langgraph_traditional_explicit",
        planned_execution=False,
        analysis_method="phase3",
    )

    service._store_intelligence_results("C1", "tenant_a", "gemini-2.5-flash", intelligence)

    query, params = fake_graph.query.call_args.args
    assert "CREATE (a:AnalysisRun" in query
    assert "HAS_ANALYSIS" in query
    assert "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'" in query
    stored = json.loads(field_encryptor.decrypt(params["result_payload"]))
    assert stored["execution_path"] == "langgraph_traditional_explicit"
    assert stored["model_used"] == "gemini-2.5-flash"
