from abc import ABC, abstractmethod
from typing import Any, List, Dict
from backend.agents.reranker_service import RerankerService
from backend.domain.search_entities import SearchParams, SearchResult
from backend.infrastructure.encryption import field_encryptor
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.utils.contract_search_tool import graph, embedding
from backend.shared.utils.logger import get_logger
from backend.shared.utils.utils import convert_neo4j_date
from backend.shared.utils.vector_index_config import (
    CONTRACT_EMBEDDING_INDEX, CLAUSE_EMBEDDING_INDEX, SECTION_EMBEDDING_INDEX,
    VECTOR_SEARCH_OVERFETCH, RERANK_POOL_SIZE, RERANK_TOP_K,
)

logger = get_logger(__name__)

class SearchStrategy(ABC):
    """Abstract base class for search strategies (Strategy Pattern)"""

    @abstractmethod
    def execute(self, params: SearchParams) -> SearchResult:
        pass


def _cypher_page_size(params: SearchParams) -> int:
    """
    RERANK_POOL_SIZE (wider) when re-ranking will actually run for this
    request (flag on AND real query text present - re-ranking an
    unfiltered/browse-all listing with no query has nothing to judge
    relevance against), else the original page size. No reason to overfetch
    a wider candidate pool from Neo4j just to immediately truncate it back
    down when there is nothing to rerank with.
    """
    if Phase3Config.RERANKING_ENABLED and params.query:
        return RERANK_POOL_SIZE
    return RERANK_TOP_K


def _maybe_rerank(params: SearchParams, items: List[Dict[str, Any]], text_key: str, metadata: Dict[str, Any]):
    """
    Applies re-ranking to `items` (the Cypher already returned a
    RERANK_POOL_SIZE-wide pool in this case - see _cypher_page_size) when
    the feature flag is on and a real query was given, truncating to
    RERANK_TOP_K either way so callers get a consistently-sized page
    regardless of whether reranking ran. Injects an explainability block
    into search_metadata (not just into each item's original_rank/
    reranked_rank/relevance_score) so a caller can tell at a glance whether
    reranking actually engaged for this response.
    """
    if not (Phase3Config.RERANKING_ENABLED and params.query and items):
        return items[:RERANK_TOP_K], metadata

    try:
        # use_fallback=True (not get_reranker_llm(), Gemini-only) - real
        # multi-provider fallback (backend/agents/llm_fallback_service.py),
        # so a Gemini-specific outage/quota exhaustion degrades to a
        # different provider before falling all the way back to unranked
        # results.
        outcome = RerankerService(use_fallback=True).rerank(params.query, items, text_key=text_key, top_k=RERANK_TOP_K)
    except Exception as e:
        # RerankerService.rerank() already catches failures *inside* its own
        # LLM call and degrades gracefully (RerankOutcome.reranked=False) -
        # but RerankerService.__init__() itself raising (e.g. a
        # construction-time error) happens before any of that internal
        # safety net exists. Without this try/except, that exception would
        # propagate uncaught and crash the whole search request - a real
        # gap found live while extending re-ranking to more search levels,
        # and just as real for the original Document/Clause wiring this
        # helper already served. Search must never hard-fail because
        # re-ranking failed, full stop.
        logger.error(f"Re-ranking setup failed, falling back to unranked results: {e}")
        return items[:RERANK_TOP_K], {**metadata, "reranking": {"applied": False, "reason": "error"}}

    metadata = {**metadata, "reranking": {"applied": outcome.reranked, "reason": outcome.reason}}
    return outcome.results, metadata

class DocumentSearchStrategy(SearchStrategy):
    """Document-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {"tenant_id": params.tenant_id}
            # tenant_id first and unconditional (not appended alongside the
            # optional filters below) - every branch of this query, vector
            # or not, must carry it. Previously absent entirely: SearchParams
            # had no tenant_id field, so this endpoint returned every
            # tenant's contracts to any authenticated caller regardless of
            # role - a live, currently-reachable cross-tenant leak, found and
            # fixed the same day the reranking work below was requested,
            # since reranking cannot honestly claim to preserve tenant
            # isolation "as strictly as existing search" when existing
            # search had none.
            filters = ["c.tenant_id = $tenant_id", "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'"]

            # Apply all filters
            if params.active is not None:
                operator = ">=" if params.active else "<"
                filters.append(f"c.end_date {operator} date()")

            if params.contract_type:
                filters.append("toLower(c.contract_type) CONTAINS toLower($contract_type)")
                cypher_params["contract_type"] = params.contract_type

            if params.min_effective_date:
                filters.append("c.effective_date >= date($min_effective_date)")
                cypher_params["min_effective_date"] = params.min_effective_date

            if params.max_effective_date:
                filters.append("c.effective_date <= date($max_effective_date)")
                cypher_params["max_effective_date"] = params.max_effective_date

            if params.min_end_date:
                filters.append("c.end_date >= date($min_end_date)")
                cypher_params["min_end_date"] = params.min_end_date

            if params.max_end_date:
                filters.append("c.end_date <= date($max_end_date)")
                cypher_params["max_end_date"] = params.max_end_date

            # Add semantic search if query provided - queries the vector
            # index for the top VECTOR_SEARCH_OVERFETCH candidates (instead
            # of scoring every Contract node), then applies the same
            # non-vector filters afterward.
            if params.query:
                query_embedding = embedding.embed_query(params.query)
                cypher_params["query_embedding"] = query_embedding
                cypher_params["k"] = VECTOR_SEARCH_OVERFETCH

                cypher_statement = f"""
                CALL db.index.vector.queryNodes('{CONTRACT_EMBEDDING_INDEX}', $k, $query_embedding)
                YIELD node AS c, score
                WHERE score > 0.3
                """
                if filters:
                    cypher_statement += f"AND {' AND '.join(filters)} "
                cypher_statement += "ORDER BY score DESC "
            else:
                cypher_statement = "MATCH (c:Contract) "
                if filters:
                    cypher_statement += f"WHERE {' AND '.join(filters)} "

            # Page size: RERANK_POOL_SIZE (wider candidate pool) when
            # re-ranking will actually run for this request, else the
            # original top-10 page - see _cypher_page_size.
            cypher_params["page_size"] = _cypher_page_size(params)
            cypher_statement += """
            RETURN {
                total_count: count(c),
                contracts: collect({
                    file_id: c.file_id,
                    summary: c.summary,
                    contract_type: c.contract_type,
                    effective_date: c.effective_date,
                    end_date: c.end_date,
                    parties: [(c)<-[r:PARTY_TO]-(party) | {name: party.name, role: r.role}]
                })[..$page_size]
            } AS result
            """

            output = graph.query(cypher_statement, cypher_params)

            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                contracts = [convert_neo4j_date(contract) for contract in result_data.get("contracts", [])]
                metadata = {"search_level": "document", "query": params.query}
                contracts, metadata = _maybe_rerank(params, contracts, text_key="summary", metadata=metadata)
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=contracts,
                    search_metadata=metadata
                )

            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "document"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "document", "error": str(e)})

class ClauseSearchStrategy(SearchStrategy):
    """Clause-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {"tenant_id": params.tenant_id}
            # See DocumentSearchStrategy's identical comment - same fix,
            # same real leak, same root cause (SearchParams had no
            # tenant_id field at all).
            filters = ["c.tenant_id = $tenant_id", "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'"]

            if params.clause_types:
                filters.append("cl.clause_type IN $clause_types")
                cypher_params["clause_types"] = params.clause_types

            if params.query:
                query_embedding = embedding.embed_query(params.query)
                cypher_params["query_embedding"] = query_embedding
                cypher_params["k"] = VECTOR_SEARCH_OVERFETCH

                cypher_statement = f"""
                CALL db.index.vector.queryNodes('{CLAUSE_EMBEDDING_INDEX}', $k, $query_embedding)
                YIELD node AS cl, score
                MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl)
                WHERE score > 0.3
                """
                if filters:
                    cypher_statement += f"AND {' AND '.join(filters)} "
                cypher_statement += "ORDER BY score DESC "
            else:
                cypher_statement = "MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl:Clause) "
                if filters:
                    cypher_statement += f"WHERE {' AND '.join(filters)} "

            cypher_params["page_size"] = _cypher_page_size(params)
            cypher_statement += """
            RETURN {
                total_count: count(cl),
                clauses: collect({
                    contract_id: c.file_id,
                    clause_type: cl.clause_type,
                    content: cl.content,
                    confidence: cl.confidence
                })[..$page_size]
            } AS result
            """

            output = graph.query(cypher_statement, cypher_params)

            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                clauses = [convert_neo4j_date(clause) for clause in result_data.get("clauses", [])]
                # cl.content is encrypted at rest (clause_repository.py's
                # write path: PIIEngine.redact then field_encryptor.encrypt)
                # - this raw Cypher read bypassed that repository's own
                # decrypt-on-read entirely, so this endpoint was returning
                # base64 ciphertext as "clause content" to the real caller.
                # Found and fixed in passing while adding tenant_id above.
                # Decrypted BEFORE re-ranking - re-ranking a still-encrypted
                # candidate would score ciphertext, not real clause text.
                for clause in clauses:
                    clause["content"] = field_encryptor.decrypt(clause.get("content") or "")
                metadata = {"search_level": "clause", "clause_types": params.clause_types}
                clauses, metadata = _maybe_rerank(params, clauses, text_key="content", metadata=metadata)
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=clauses,
                    search_metadata=metadata
                )

            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "clause"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "clause", "error": str(e)})

class SectionSearchStrategy(SearchStrategy):
    """Section-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {"tenant_id": params.tenant_id}
            # See DocumentSearchStrategy's identical comment - same fix,
            # same real leak, same root cause.
            filters = ["c.tenant_id = $tenant_id", "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'"]

            if params.section_types:
                filters.append("s.section_type IN $section_types")
                cypher_params["section_types"] = params.section_types

            if params.query:
                query_embedding = embedding.embed_query(params.query)
                cypher_params["query_embedding"] = query_embedding
                cypher_params["k"] = VECTOR_SEARCH_OVERFETCH

                cypher_statement = f"""
                CALL db.index.vector.queryNodes('{SECTION_EMBEDDING_INDEX}', $k, $query_embedding)
                YIELD node AS s, score
                MATCH (c:Contract)-[:HAS_SECTION]->(s)
                WHERE score > 0.3
                """
                if filters:
                    cypher_statement += f"AND {' AND '.join(filters)} "
                cypher_statement += "ORDER BY score DESC "
            else:
                cypher_statement = "MATCH (c:Contract)-[:HAS_SECTION]->(s:Section) "
                if filters:
                    cypher_statement += f"WHERE {' AND '.join(filters)} "

            cypher_params["page_size"] = _cypher_page_size(params)
            cypher_statement += """
            RETURN {
                total_count: count(s),
                sections: collect({
                    contract_id: c.file_id,
                    section_type: s.section_type,
                    content: s.content,
                    order: s.order
                })[..$page_size]
            } AS result
            """

            output = graph.query(cypher_statement, cypher_params)

            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                sections = [convert_neo4j_date(section) for section in result_data.get("sections", [])]
                metadata = {"search_level": "section", "section_types": params.section_types}
                sections, metadata = _maybe_rerank(params, sections, text_key="content", metadata=metadata)
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=sections,
                    search_metadata=metadata
                )
            
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "section"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "section", "error": str(e)})

class RelationshipSearchStrategy(SearchStrategy):
    """Relationship-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {"tenant_id": params.tenant_id}
            # See DocumentSearchStrategy's identical comment - same fix,
            # same real leak, same root cause.
            filters = ["c.tenant_id = $tenant_id", "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'"]

            cypher_statement = "MATCH (c:Contract)<-[r:PARTY_TO]-(p:Party) "

            if params.parties:
                filters.append("p.name IN $parties")
                cypher_params["parties"] = params.parties
            
            if params.query:
                query_embedding = embedding.embed_query(params.query)
                cypher_params["query_embedding"] = query_embedding
                
                cypher_statement += """
                WHERE r.embedding IS NOT NULL AND vector.similarity.cosine(r.embedding, $query_embedding) > 0.3
                """
                
                if filters:
                    cypher_statement += f"AND {' AND '.join(filters)} "
            elif filters:
                cypher_statement += f"WHERE {' AND '.join(filters)} "
            
            cypher_params["page_size"] = _cypher_page_size(params)
            cypher_statement += """
            RETURN {
                total_count: count(r),
                relationships: collect({
                    contract_id: c.file_id,
                    party_name: p.name,
                    role: r.role,
                    context: r.context
                })[..$page_size]
            } AS result
            """

            output = graph.query(cypher_statement, cypher_params)

            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                relationships = [convert_neo4j_date(rel) for rel in result_data.get("relationships", [])]
                metadata = {"search_level": "relationship", "parties": params.parties}
                relationships, metadata = _maybe_rerank(params, relationships, text_key="context", metadata=metadata)
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=relationships,
                    search_metadata=metadata
                )
            
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "relationship"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "relationship", "error": str(e)})
