import hashlib
import json
from typing import Any, Dict, List, Optional, Type
from enum import Enum
from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from backend.shared.utils.gemini_embedding_service import embedding
from langchain_neo4j import Neo4jGraph
from pydantic import BaseModel, Field
from backend.agents.reranker_service import RerankerService
from backend.infrastructure.encryption import field_encryptor
from backend.shared.cache.redis_cache import cache
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.utils.vector_index_config import (
    CONTRACT_EMBEDDING_INDEX, SECTION_EMBEDDING_INDEX, CLAUSE_EMBEDDING_INDEX,
    CHUNK_EMBEDDING_INDEX, CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT,
    RERANK_POOL_SIZE, RERANK_TOP_K,
)
from backend.shared.utils.dynamic_retrieval import (
    DYNAMIC_RETRIEVAL_TOP_K, DYNAMIC_RETRIEVAL_FLOOR, apply_dynamic_score_filter,
)
from backend.shared.utils.logger import get_logger

logger = get_logger(__name__)

load_dotenv()

from .utils import convert_neo4j_date

class SearchLevel(str, Enum):
    DOCUMENT = "document"
    SECTION = "section"
    CLAUSE = "clause"
    RELATIONSHIP = "relationship"
    CHUNK = "chunk"
    ALL = "all"

class NumberOperator(str, Enum):
    EQUALS = "="
    GREATER_THAN = ">"
    LESS_THAN = "<"

class MonetaryValue(BaseModel):
    value: float
    operator: NumberOperator

class Location(BaseModel):
    country: Optional[str] = Field(None, description="Use two-letter ISO standard")
    state: Optional[str]

_graph_instance = None

def get_graph() -> Neo4jGraph:
    global _graph_instance
    if _graph_instance is None:
        _graph_instance = Neo4jGraph(
            refresh_schema=False, driver_config={"notifications_min_severity": "OFF"}
        )
    return _graph_instance
# embedding imported from gemini_embedding_service (1536 dimensions)

def _cypher_page_size(summary_search: Optional[str]) -> int:
    """
    Mirrors search_strategies.py's identical helper: RERANK_POOL_SIZE
    (wider) when re-ranking will actually run for this request (flag on
    AND real query text present), else the original page size this file
    has always used (10) - no reason to overfetch a wider candidate pool
    just to immediately truncate it back down when there's nothing to
    rerank with.
    """
    if Phase3Config.RERANKING_ENABLED and summary_search:
        return RERANK_POOL_SIZE
    return 10


def _maybe_rerank(query: Optional[str], items: List[Dict[str, Any]], text_key: str):
    """
    Mirrors search_strategies.py's identical helper, adapted to this
    file's flat-kwargs calling convention (no SearchParams/SearchResult
    dataclasses here - see docs/CAPSTONE_SUMMARY.md's discussion of why
    this file stays a separate implementation rather than sharing those
    types). Returns (items, reranking_metadata_or_None) - callers only
    add a "reranking" key to their result dict when metadata is not None,
    so the response shape is byte-for-byte unchanged when the flag is off
    or there's no query to rerank against.
    """
    if not (Phase3Config.RERANKING_ENABLED and query and items):
        return items[:RERANK_TOP_K], None
    try:
        # use_fallback=True - see search_strategies.py's identical
        # _maybe_rerank comment (real multi-provider fallback, backend/
        # agents/llm_fallback_service.py).
        outcome = RerankerService(use_fallback=True).rerank(query, items, text_key=text_key, top_k=RERANK_TOP_K)
    except Exception as e:
        # See search_strategies.py's identical _maybe_rerank comment: a
        # construction-time failure (RerankerService.__init__ itself
        # raising, before RerankerService.rerank()'s own internal safety
        # net exists) must not crash the whole search.
        logger.error(f"Re-ranking setup failed, falling back to unranked results: {e}")
        return items[:RERANK_TOP_K], {"applied": False, "reason": "error"}
    return outcome.results, {"applied": outcome.reranked, "reason": outcome.reason}


def _multi_level_search_cache_key(
    tenant_id: str, search_level: SearchLevel, clause_types, section_types,
    min_effective_date, max_effective_date, min_end_date, max_end_date,
    contract_type, parties, summary_search, active, cypher_aggregation,
    monetary_value, governing_law, contract_id=None,
) -> str:
    """
    Deterministic key over every argument that affects the result *except*
    `embeddings` (always the same singleton service object - not itself
    meaningful, and not JSON-serializable) - re-embedding identical query
    text deterministically produces the same vector anyway, so keying on
    the raw text is equivalent and avoids embedding on a cache hit at all.
    Same explicit-hash approach as LLMExtractionService._cache_key /
    PolicyEvaluationService._cache_key, rather than the generic
    @cache_result decorator (which would hash the `embeddings` object's
    repr directly if applied to this function).
    """
    key_data = {
        "tenant_id": tenant_id, "search_level": search_level.value, "contract_id": contract_id,
        "clause_types": sorted(clause_types) if clause_types else None,
        "section_types": sorted(section_types) if section_types else None,
        "min_effective_date": min_effective_date, "max_effective_date": max_effective_date,
        "min_end_date": min_end_date, "max_end_date": max_end_date,
        "contract_type": contract_type,
        "parties": sorted(parties) if parties else None,
        "summary_search": summary_search, "active": active,
        "cypher_aggregation": cypher_aggregation,
        "monetary_value": monetary_value.model_dump() if monetary_value else None,
        "governing_law": governing_law.model_dump() if governing_law else None,
        # Keyed so flipping RERANKING_ENABLED never serves a stale
        # cached order (or a stale non-reranked page size) from the
        # other setting - same requirement already applied to the REST
        # path's cache key (search_strategies.py / §18).
        "reranking_enabled": Phase3Config.RERANKING_ENABLED,
    }
    raw = f"vector_search:{json.dumps(key_data, sort_keys=True, default=str)}"
    return f"vector_search:{tenant_id}:tool:{hashlib.sha256(raw.encode()).hexdigest()}"


def get_contracts_multi_level(
    embeddings: Any,
    tenant_id: str,
    search_level: SearchLevel = SearchLevel.ALL,
    clause_types: Optional[List[str]] = None,
    section_types: Optional[List[str]] = None,
    min_effective_date: Optional[str] = None,
    max_effective_date: Optional[str] = None,
    min_end_date: Optional[str] = None,
    max_end_date: Optional[str] = None,
    contract_type: Optional[str] = None,
    parties: Optional[List[str]] = None,
    summary_search: Optional[str] = None,
    active: Optional[bool] = None,
    cypher_aggregation: Optional[str] = None,
    monetary_value: Optional[MonetaryValue] = None,
    governing_law: Optional[Location] = None,
    contract_id: Optional[str] = None,
):
    """Enhanced contract search with multi-level embedding support and tenant isolation.

    Caches the real vector-index (db.index.vector.queryNodes) retrieval
    path via Redis - the same infra already used for precedent_clause/
    deviation_analysis/jurisdiction_analysis (shared/cache/redis_cache.py),
    keyed on every filter that affects the result, not just the query text.
    """
    # search_level arrives as a plain str, not a SearchLevel enum, whenever
    # this is reached through Contract Chat: EnhancedContractSearchTool is
    # tenant-scoped, so contract_chat_agent.py's execute_tools calls
    # tool._run(**args) directly with the LLM's raw tool-call args,
    # deliberately bypassing args_schema's pydantic validation/coercion
    # (the same bypass that keeps tenant_id un-spoofable - see
    # EnhancedContractInput's docstring). A real, pre-existing crash found
    # live while verifying this session's re-ranking work through Contract
    # Chat: _multi_level_search_cache_key's search_level.value raised
    # AttributeError on a plain str. The dispatch below (search_level ==
    # SearchLevel.X) already worked either way since SearchLevel is a str
    # Enum, but .value access does not - normalizing once here fixes both.
    if not isinstance(search_level, SearchLevel):
        search_level = SearchLevel(search_level)

    cache_key = _multi_level_search_cache_key(
        tenant_id, search_level, clause_types, section_types,
        min_effective_date, max_effective_date, min_end_date, max_end_date,
        contract_type, parties, summary_search, active, cypher_aggregation,
        monetary_value, governing_law, contract_id,
    )
    if Phase3Config.CACHE_ENABLED:
        cached = cache.get(cache_key)
        if cached is not None:
            return cached

    params: dict[str, Any] = {"tenant_id": tenant_id}
    filters: list[str] = [
        "c.tenant_id = $tenant_id",
        "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'",
    ]
    if contract_id:
        filters.append("c.file_id = $contract_id")
        params["contract_id"] = contract_id

    if search_level == SearchLevel.DOCUMENT:
        result = _search_documents(embeddings, tenant_id, summary_search, filters, params,
                               min_effective_date, max_effective_date, min_end_date, max_end_date,
                               contract_type, parties, active, cypher_aggregation, monetary_value, governing_law)

    elif search_level == SearchLevel.SECTION:
        result = _search_sections(embeddings, tenant_id, summary_search, section_types, filters, params, contract_type=contract_type, parties=parties)

    elif search_level == SearchLevel.CLAUSE:
        result = _search_clauses(embeddings, tenant_id, summary_search, clause_types, filters, params, contract_type=contract_type, parties=parties)

    elif search_level == SearchLevel.RELATIONSHIP:
        result = _search_relationships(embeddings, tenant_id, summary_search, parties, filters, params, contract_type=contract_type)

    elif search_level == SearchLevel.CHUNK:
        result = _search_chunks(embeddings, tenant_id, summary_search, filters, params, contract_id=contract_id, contract_type=contract_type, parties=parties)

    elif search_level == SearchLevel.ALL:
        result = _search_all_levels(embeddings, tenant_id, summary_search, clause_types, section_types,
                                 filters, params, contract_id=contract_id, contract_type=contract_type, parties=parties)
    else:
        result = None

    if Phase3Config.CACHE_ENABLED and result is not None:
        cache.set(cache_key, result, ttl=Phase3Config.get_cache_ttl("vector_search"))

    return result

def _search_documents(embeddings, tenant_id, summary_search, filters, params, 
                     min_effective_date, max_effective_date, min_end_date, max_end_date,
                     contract_type, parties, active, cypher_aggregation, monetary_value, governing_law):
    """Search at document level using existing logic with tenant isolation"""
    # Apply existing filters (already includes tenant_id filter from get_contracts_multi_level)
    if governing_law and governing_law.country:
        filters.append("""EXISTS {
            MATCH (c)-[:HAS_GOVERNING_LAW]->(country)
            WHERE toLower(country.country) = $governing_law_country
        }""")
        params["governing_law_country"] = governing_law.country.lower()

    if monetary_value:
        filters.append(f"c.total_amount {monetary_value.operator.value} $total_value")
        params["total_value"] = monetary_value.value

    if min_effective_date:
        filters.append("c.effective_date >= date($min_effective_date)")
        params["min_effective_date"] = min_effective_date

    if max_effective_date:
        filters.append("c.effective_date <= date($max_effective_date)")
        params["max_effective_date"] = max_effective_date

    if active is not None:
        operator = ">=" if active else "<"
        filters.append(f"c.end_date {operator} date()")

    if contract_type:
        filters.append("toUpper(c.contract_type) CONTAINS toUpper($contract_type)")
        params["contract_type"] = contract_type

    if parties:
        filters.append("EXISTS { MATCH (c)<-[:PARTY_TO]-(p:Party) WHERE any(party IN $parties WHERE toLower(p.name) CONTAINS toLower(party)) }")
        params["parties"] = [p.lower() for p in parties]

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        # Dynamic relative filtering (top-K then score-delta, see
        # dynamic_retrieval.py) replaces the old fixed "doc_score > 0.8" -
        # only a small top-K candidate pool is fetched here, so $k is the
        # dynamic-retrieval K, not the broader VECTOR_SEARCH_OVERFETCH used
        # elsewhere for a pre-any-filtering overfetch.
        params["k"] = DYNAMIC_RETRIEVAL_TOP_K
        params["score_floor"] = DYNAMIC_RETRIEVAL_FLOOR

        # Query the vector index for candidates (instead of scoring every
        # Contract node), then apply the same non-vector filters afterward -
        # a vector index query has no way to pre-filter by an arbitrary
        # property before ranking globally.
        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{CONTRACT_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS c, score AS doc_score
        WHERE doc_score > $score_floor
        """
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
        cypher_statement += "ORDER BY doc_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

    params["page_size"] = _cypher_page_size(summary_search)
    relevance_field = ", relevance_score: doc_score" if summary_search else ""
    cypher_statement += f"""
    RETURN {{
        total_count: count(c),
        contracts: collect({{
            file_id: c.file_id,
            filename: c.filename,
            summary: c.summary,
            contract_type: c.contract_type,
            effective_date: c.effective_date,
            end_date: c.end_date,
            parties: [(c)<-[r:PARTY_TO]-(party) | {{name: party.name, role: r.role}}]{relevance_field}
        }})
    }} AS result
    """

    output = get_graph().query(cypher_statement, params)
    converted = [convert_neo4j_date(el) for el in output]
    if converted and "result" in converted[0]:
        result_data = converted[0]["result"]
        contracts = result_data.get("contracts", [])
        if summary_search:
            contracts = apply_dynamic_score_filter(contracts)
        contracts = contracts[:params["page_size"]]
        for contract in contracts:
            contract.pop("relevance_score", None)
        contracts, reranking = _maybe_rerank(summary_search, contracts, text_key="summary")
        result_data["contracts"] = contracts
        if reranking is not None:
            result_data["reranking"] = reranking
    return converted

def _safe_decrypt(val: Optional[str]) -> str:
    if not val:
        return ""
    try:
        return field_encryptor.decrypt(val)
    except Exception:
        return val

def _search_sections(embeddings, tenant_id, summary_search, section_types, filters, params, contract_type=None, parties=None):
    """Search at section level with tenant isolation and metadata filtering"""
    filters.append("c.tenant_id = $tenant_id")

    if section_types:
        filters.append("s.section_type IN $section_types")
        params["section_types"] = section_types

    if contract_type:
        filters.append("(toUpper(c.contract_type) CONTAINS toUpper($contract_type) OR toUpper(c.filename) CONTAINS toUpper($contract_type))")
        params["contract_type"] = contract_type

    if parties:
        filters.append("(EXISTS { MATCH (c)<-[:PARTY_TO]-(p:Party) WHERE any(party IN $parties WHERE toLower(p.name) CONTAINS toLower(party)) } OR any(party IN $parties WHERE toLower(c.filename) CONTAINS toLower(party)))")
        params["parties"] = [p.lower() for p in parties]

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        # Dynamic relative filtering, see dynamic_retrieval.py - replaces
        # the old fixed "section_score > 0.65".
        params["k"] = DYNAMIC_RETRIEVAL_TOP_K
        params["score_floor"] = DYNAMIC_RETRIEVAL_FLOOR

        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{SECTION_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS s, score AS section_score
        MATCH (c:Contract)-[:HAS_SECTION]->(s)
        WHERE section_score > $score_floor
        """
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
        cypher_statement += "ORDER BY section_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract)-[:HAS_SECTION]->(s:Section) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

    params["page_size"] = _cypher_page_size(summary_search)
    relevance_field = ", relevance_score: section_score" if summary_search else ""
    cypher_statement += f"""
    RETURN {{
        total_count: count(s),
        sections: collect({{
            contract_id: c.file_id,
            filename: c.filename,
            section_id: s.section_id,
            section_type: s.section_type,
            content: s.content,
            order: s.order{relevance_field}
        }})
    }} AS result
    """

    output = get_graph().query(cypher_statement, params)
    converted = [convert_neo4j_date(el) for el in output]
    if converted and "result" in converted[0]:
        result_data = converted[0]["result"]
        sections = result_data.get("sections", [])
        if summary_search:
            sections = apply_dynamic_score_filter(sections)
        sections = sections[:params["page_size"]]
        for sec in sections:
            sec.pop("relevance_score", None)
            sec["content"] = _safe_decrypt(sec.get("content"))
        sections, reranking = _maybe_rerank(summary_search, sections, text_key="content")
        result_data["sections"] = sections
        if reranking is not None:
            result_data["reranking"] = reranking
    return converted

def _search_clauses(embeddings, tenant_id, summary_search, clause_types, filters, params, contract_type=None, parties=None):
    """Search at clause level with tenant isolation and metadata filtering"""
    filters.append("c.tenant_id = $tenant_id")

    if clause_types:
        filters.append("cl.clause_type IN $clause_types")
        params["clause_types"] = clause_types

    if contract_type:
        filters.append("(toUpper(c.contract_type) CONTAINS toUpper($contract_type) OR toUpper(c.filename) CONTAINS toUpper($contract_type))")
        params["contract_type"] = contract_type

    if parties:
        filters.append("(EXISTS { MATCH (c)<-[:PARTY_TO]-(p:Party) WHERE any(party IN $parties WHERE toLower(p.name) CONTAINS toLower(party)) } OR any(party IN $parties WHERE toLower(c.filename) CONTAINS toLower(party)))")
        params["parties"] = [p.lower() for p in parties]

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        # Dynamic relative filtering, see dynamic_retrieval.py - replaces
        # the old fixed "clause_score > 0.65".
        params["k"] = DYNAMIC_RETRIEVAL_TOP_K
        params["score_floor"] = DYNAMIC_RETRIEVAL_FLOOR

        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{CLAUSE_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS cl, score AS clause_score
        MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl)
        WHERE clause_score > $score_floor
        """
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
        cypher_statement += "ORDER BY clause_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl:Clause) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

    params["page_size"] = _cypher_page_size(summary_search)
    relevance_field = ", relevance_score: clause_score" if summary_search else ""
    cypher_statement += f"""
    RETURN {{
        total_count: count(cl),
        clauses: collect({{
            contract_id: c.file_id,
            filename: c.filename,
            clause_id: cl.clause_id,
            clause_type: cl.clause_type,
            content: cl.content,
            confidence: cl.confidence,
            start_position: cl.start_position,
            end_position: cl.end_position{relevance_field}
        }})
    }} AS result
    """

    output = get_graph().query(cypher_statement, params)
    converted = [convert_neo4j_date(el) for el in output]
    if converted and "result" in converted[0]:
        result_data = converted[0]["result"]
        clauses = result_data.get("clauses", [])
        if summary_search:
            clauses = apply_dynamic_score_filter(clauses)
        clauses = clauses[:params["page_size"]]
        for clause in clauses:
            clause.pop("relevance_score", None)
            clause["content"] = _safe_decrypt(clause.get("content"))
        clauses, reranking = _maybe_rerank(summary_search, clauses, text_key="content")
        result_data["clauses"] = clauses
        if reranking is not None:
            result_data["reranking"] = reranking
    return converted

def _search_relationships(embeddings, tenant_id, summary_search, parties, filters, params, contract_type=None):
    """Search at relationship level with tenant isolation and metadata filtering"""
    cypher_statement = "MATCH (c:Contract)<-[r:PARTY_TO]-(p:Party) "
    
    filters.append("c.tenant_id = $tenant_id")
    
    if contract_type:
        filters.append("(toUpper(c.contract_type) CONTAINS toUpper($contract_type) OR toUpper(c.filename) CONTAINS toUpper($contract_type))")
        params["contract_type"] = contract_type

    if parties:
        filters.append("(p.name IN $parties OR any(party IN $parties WHERE toLower(p.name) CONTAINS toLower(party)) OR any(party IN $parties WHERE toLower(c.filename) CONTAINS toLower(party)))")
        params["parties"] = [p.lower() for p in parties]
    
    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        # Dynamic relative filtering, see dynamic_retrieval.py - replaces
        # the old fixed "> 0.65". No native vector-index top-K here (this
        # is an inline cosine computation, not db.index.vector.queryNodes),
        # so ORDER BY + LIMIT stands in for the top-K step.
        params["score_floor"] = DYNAMIC_RETRIEVAL_FLOOR
        params["top_k"] = DYNAMIC_RETRIEVAL_TOP_K

        cypher_statement += """
        WHERE r.embedding IS NOT NULL AND vector.similarity.cosine(r.embedding, $summary_embedding) > $score_floor
        """

        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
    elif filters:
        cypher_statement += f"WHERE {' AND '.join(filters)} "

    params["page_size"] = _cypher_page_size(summary_search)
    if summary_search:
        cypher_statement += """
        WITH c, r, p, vector.similarity.cosine(r.embedding, $summary_embedding) AS rel_score
        ORDER BY rel_score DESC
        LIMIT $top_k
        """
        relevance_field = ", relevance_score: rel_score"
    else:
        relevance_field = ""
    cypher_statement += f"""
    RETURN {{
        total_count: count(r),
        relationships: collect({{
            contract_id: c.file_id,
            filename: c.filename,
            party_name: p.name,
            role: r.role,
            context: r.context{relevance_field}
        }})
    }} AS result
    """

    output = get_graph().query(cypher_statement, params)
    converted = [convert_neo4j_date(el) for el in output]
    if converted and "result" in converted[0]:
        result_data = converted[0]["result"]
        relationships = result_data.get("relationships", [])
        if summary_search:
            relationships = apply_dynamic_score_filter(relationships)
        relationships = relationships[:params["page_size"]]
        for relationship in relationships:
            relationship.pop("relevance_score", None)
        relationships, reranking = _maybe_rerank(summary_search, relationships, text_key="context")
        result_data["relationships"] = relationships
        if reranking is not None:
            result_data["reranking"] = reranking
    return converted

def _chunk_snippet(content: str) -> str:
    """Real, confirmed bug found live during a full Contract Chat
    functional audit: this used to truncate to 200 characters (a leftover
    equivalent of the old substring(content, 0, 200) Cypher snippet, from
    before Chunk.content was encrypted at rest - P3 item 21). A real
    question containing a section title verbatim from the document
    ("Fees & Invoicing") correctly matched the right chunk (real,
    confirmed: similarity_score 0.78), but the answer text - "4. Fees &
    Invoicing\\nTotal project fee: $500,000." - sat at character ~400 of a
    1405-character chunk, well past the 200-character cutoff, so it never
    reached the model at all despite the retrieval itself working
    correctly. Chunks are already a bounded, chunking-pipeline-sized unit
    (not whole documents) - truncating them a second time down to a
    "preview" defeats the actual purpose of chunk-level search, which is
    to give the model real content to answer from. Returns the chunk in
    full."""
    return content


def _search_chunks(embeddings, tenant_id, summary_search, filters, params, contract_id=None, contract_type=None, parties=None):
    """
    Enhanced search at chunk level with semantic capabilities, metadata filtering,
    and tenant isolation.
    """
    chunk_filters = [
        "coalesce(source_contract.lifecycle_status, 'ACTIVE') = 'ACTIVE'",
        "d.tenant_id = $tenant_id",
        # Dynamic relative filtering, see dynamic_retrieval.py - replaces
        # the old fixed "chunk_score > 0.65".
        "chunk_score > $score_floor"
    ]
    if contract_id:
        chunk_filters.append("d.contract_id = $contract_id")
    if contract_type:
        chunk_filters.append("(toUpper(source_contract.contract_type) CONTAINS toUpper($contract_type) OR toUpper(source_contract.filename) CONTAINS toUpper($contract_type))")
        params["contract_type"] = contract_type
    if parties:
        chunk_filters.append("(EXISTS { MATCH (source_contract)<-[:PARTY_TO]-(p:Party) WHERE any(party IN $parties WHERE toLower(p.name) CONTAINS toLower(party)) } OR any(party IN $parties WHERE toLower(source_contract.filename) CONTAINS toLower(party)))")
        params["parties"] = [p.lower() for p in parties]

    # Try semantic search first if available
    if summary_search:
        try:
            semantic_query = f"""
            CALL db.index.vector.queryNodes('{CHUNK_EMBEDDING_INDEX}', $k, $chunk_embedding)
            YIELD node AS c, score AS chunk_score
            MATCH (d:Document)-[:HAS_CHUNK]->(c)
            MATCH (source_contract:Contract {{file_id: d.contract_id, tenant_id: $tenant_id}})
            WHERE {' AND '.join(chunk_filters)}
            RETURN d.id AS document_id, d.contract_id AS contract_id,
                   source_contract.filename AS filename, c.id AS chunk_id,
                   c.chunk_type AS chunk_type, c.content AS content,
                   c.chunk_index AS chunk_index, c.quality_score AS quality_score,
                   c.start_position AS start_offset, c.end_position AS end_offset,
                   c.page_number AS page_number,
                   chunk_score AS similarity_score
            ORDER BY chunk_score DESC
            """

            chunk_embedding = embeddings.embed_query(summary_search)
            semantic_params = {
                "chunk_embedding": chunk_embedding,
                "tenant_id": tenant_id,
                "k": DYNAMIC_RETRIEVAL_TOP_K,
                "score_floor": DYNAMIC_RETRIEVAL_FLOOR,
                **params,
            }
            if contract_id:
                semantic_params["contract_id"] = contract_id

            rows = get_graph().query(semantic_query, semantic_params)
            rows = apply_dynamic_score_filter(rows, score_key="similarity_score")
            if rows:
                chunks = [
                    {
                        "document_id": r["document_id"],
                        "contract_id": r.get("contract_id"),
                        "filename": r.get("filename"),
                        "chunk_id": r.get("chunk_id"),
                        "chunk_type": r["chunk_type"],
                        "content": _chunk_snippet(field_encryptor.decrypt(r["content"] or "")),
                        "chunk_index": r["chunk_index"],
                        "start_offset": r.get("start_offset"),
                        "end_offset": r.get("end_offset"),
                        "page_number": r.get("page_number"),
                        "quality_score": r["quality_score"],
                        "similarity_score": r["similarity_score"],
                        "search_type": "semantic",
                    }
                    for r in rows[:10]
                ]
                result = {"total_count": len(rows), "chunks": chunks}
                return [convert_neo4j_date({"result": result})]
        except Exception as e:
            logger.error(f"Semantic chunk search failed, falling back to text search: {e}")

    # Fallback to text search across both new and legacy chunks, enforcing tenant_id.
    search_text = summary_search
    output = []

    new_chunk_query = f"""
    MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
    MATCH (source_contract:Contract {{file_id: d.contract_id, tenant_id: $tenant_id}})
    WHERE coalesce(source_contract.lifecycle_status, 'ACTIVE') = 'ACTIVE'
      AND d.tenant_id = $tenant_id
    {"AND d.contract_id = $contract_id" if contract_id else ""}
    RETURN d.id AS document_id, d.contract_id AS contract_id,
           source_contract.filename AS filename, c.id AS chunk_id,
           c.chunk_type AS chunk_type, c.content AS content,
           c.chunk_index AS chunk_index, c.quality_score AS quality_score,
           c.start_position AS start_offset, c.end_position AS end_offset,
           c.page_number AS page_number
    ORDER BY c.chunk_index DESC
    LIMIT $candidate_limit
    """
    new_chunk_params = {"tenant_id": tenant_id, "candidate_limit": CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT}
    if contract_id:
        new_chunk_params["contract_id"] = contract_id
    new_chunk_rows = get_graph().query(new_chunk_query, new_chunk_params)

    new_chunks_limit = 5 if search_text else 10
    new_chunks = []
    for r in new_chunk_rows:
        decrypted = field_encryptor.decrypt(r["content"] or "")
        if search_text and search_text not in decrypted:
            continue
        new_chunks.append({
            "document_id": r["document_id"],
            "contract_id": r.get("contract_id"),
            "filename": r.get("filename"),
            "chunk_id": r.get("chunk_id"),
            "chunk_type": r["chunk_type"],
            "content": _chunk_snippet(decrypted),
            "chunk_index": r["chunk_index"],
            "start_offset": r.get("start_offset"),
            "end_offset": r.get("end_offset"),
            "page_number": r.get("page_number"),
            "quality_score": r["quality_score"],
            "search_type": "text_new" if search_text else "recent",
        })
        if len(new_chunks) >= new_chunks_limit:
            break
    output.append({"result": {"total_count": len(new_chunks), "chunks": new_chunks}})

    if search_text:
        legacy_chunk_query = f"""
        MATCH (c:Contract)-[:CONTAINS_CHUNK]->(dc:DocumentChunk)
        WHERE c.tenant_id = $tenant_id
          AND coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'
        {"AND c.file_id = $contract_id" if contract_id else ""}
        RETURN c.file_id AS contract_id, c.filename AS filename,
               dc.id AS chunk_id, dc.chunk_type AS chunk_type, dc.content AS content,
               dc.chunk_order AS chunk_order, dc.start_offset AS start_offset,
               dc.end_offset AS end_offset, dc.confidence AS confidence
        LIMIT $candidate_limit
        """
        legacy_chunk_params = {"tenant_id": tenant_id, "candidate_limit": CHUNK_TEXT_SEARCH_CANDIDATE_LIMIT}
        if contract_id:
            legacy_chunk_params["contract_id"] = contract_id
        legacy_chunk_rows = get_graph().query(legacy_chunk_query, legacy_chunk_params)

        legacy_chunks = []
        for r in legacy_chunk_rows:
            decrypted = field_encryptor.decrypt(r["content"] or "")
            if search_text not in decrypted:
                continue
            legacy_chunks.append({
                "contract_id": r["contract_id"],
                "filename": r.get("filename"),
                "chunk_id": r.get("chunk_id"),
                "chunk_type": r["chunk_type"],
                "content": _chunk_snippet(decrypted),
                "chunk_order": r["chunk_order"],
                "start_offset": r.get("start_offset"),
                "end_offset": r.get("end_offset"),
                "confidence": r["confidence"],
                "search_type": "text_legacy",
            })
            if len(legacy_chunks) >= 5:
                break
        output.append({"result": {"total_count": len(legacy_chunks), "chunks": legacy_chunks}})

    return [convert_neo4j_date(el) for el in output]

def _search_all_levels(embeddings, tenant_id, summary_search, clause_types, section_types, filters, params, contract_id=None, contract_type=None, parties=None):
    """Search across all levels and combine results with tenant isolation."""
    base_filters = lambda: ([
        "c.tenant_id = $tenant_id",
        "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'",
        "c.file_id = $contract_id",
    ] if contract_id else [
        "c.tenant_id = $tenant_id",
        "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'",
    ])
    base_params = lambda: ({"tenant_id": tenant_id, "contract_id": contract_id} if contract_id
                            else {"tenant_id": tenant_id})
    results = {
        "documents": _search_documents(embeddings, tenant_id, summary_search, base_filters(), base_params(), None, None, None, None, contract_type, parties, None, None, None, None),
        "sections": _search_sections(embeddings, tenant_id, summary_search, section_types, base_filters(), base_params(), contract_type=contract_type, parties=parties),
        "clauses": _search_clauses(embeddings, tenant_id, summary_search, clause_types, base_filters(), base_params(), contract_type=contract_type, parties=parties),
        "relationships": _search_relationships(embeddings, tenant_id, summary_search, parties, base_filters(), base_params(), contract_type=contract_type),
        "chunks": _search_chunks(embeddings, tenant_id, summary_search, base_filters(), base_params(), contract_id=contract_id, contract_type=contract_type, parties=parties)
    }
    return [results]

class EnhancedContractInput(BaseModel):
    # Real, confirmed bug found live: this used to default to
    # SearchLevel.DOCUMENT, and its description gave the model no real
    # guidance on what each level actually searches (it didn't even
    # mention 'chunk' as an option). 'document' only searches each
    # contract's short AI-generated summary blurb - never the real
    # contract text - so the model routinely picked (or, more often,
    # simply omitted search_level and silently fell through to) the one
    # level guaranteed not to find real content. Confirmed live: a
    # question containing a section title verbatim from the document
    # ("Fees & Invoicing") returned total_count: 0, because 'document'
    # was searched, not 'chunk'. Fixed by defaulting to SearchLevel.ALL
    # (already proven to aggregate every level, chunks included, in one
    # call) and by writing a description that actually explains what
    # each level searches, not just enumerating the enum values.
    search_level: Optional[SearchLevel] = Field(
        SearchLevel.ALL,
        description=(
            "Which level(s) to search - these search fundamentally different data, not just "
            "different levels of the same data:\n"
            "- 'document': ONLY each contract's short AI-generated summary paragraph. Does NOT "
            "search the real contract text. Use only for metadata-style questions (contract type, "
            "parties, dates, monetary value) - never for a question about actual wording, terms, "
            "or a specific clause/section.\n"
            "- 'chunk': the real, verbatim paragraphs of the actual uploaded contract. Use this for "
            "ANY question about what the contract actually says, including exact section titles, "
            "specific terms, dollar amounts, deadlines, or quoted language.\n"
            "- 'section' / 'clause' / 'relationship': structured extractions that only exist after "
            "a full contract analysis has been run on that document - frequently empty for a "
            "freshly uploaded contract, even though 'chunk' already has real content for it.\n"
            "- 'all' (default): searches every level above in one call. This is the safe default "
            "for any real content question, and never worse than picking a single level - if in "
            "doubt, or for any question about what the contract actually says, do not set this "
            "field at all and let it default to 'all'."
        ),
    )
    clause_types: Optional[List[str]] = Field(None, description="Specific CUAD clause types to search")
    section_types: Optional[List[str]] = Field(None, description="Document sections to focus on: payment, termination, liability, etc.")

    # Existing fields
    min_effective_date: Optional[str] = Field(None, description="Earliest contract effective date (YYYY-MM-DD)")
    max_effective_date: Optional[str] = Field(None, description="Latest contract effective date (YYYY-MM-DD)")
    min_end_date: Optional[str] = Field(None, description="Earliest contract end date (YYYY-MM-DD)")
    max_end_date: Optional[str] = Field(None, description="Latest contract end date (YYYY-MM-DD)")
    contract_type: Optional[str] = Field(None, description="Contract type")
    parties: Optional[List[str]] = Field(None, description="List of parties involved in the contract")
    summary_search: Optional[str] = Field(
        None,
        description=(
            "The search text to match against contract content. What this actually searches "
            "depends entirely on search_level: at 'chunk' or 'all' level (the default) it searches "
            "the real, verbatim contract paragraphs; at 'document' level it searches ONLY each "
            "contract's short AI-generated summary, not the real text - a query can legitimately "
            "find nothing at 'document' level while the exact same text is present verbatim in the "
            "contract at 'chunk' level."
        ),
    )
    active: Optional[bool] = Field(None, description="Whether the contract is active")
    governing_law: Optional[Location] = Field(None, description="Governing law of the contract")
    monetary_value: Optional[MonetaryValue] = Field(None, description="The total amount or value of a contract")
    cypher_aggregation: Optional[str] = Field(None, description="Custom Cypher statement for advanced aggregations")

    # tenant_id is deliberately NOT a field here. It previously was
    # (`Field(..., description=...)`), which meant the LLM had to supply it
    # itself as a tool-call argument - it has no legitimate way to know the
    # real authenticated tenant_id, so in practice it fabricated a
    # plausible-looking placeholder ("default_tenant_id", observed live),
    # silently scoping every chat search to a tenant that doesn't exist.
    # The real tenant_id is now injected server-side by contract_chat_
    # agent.py's execute_tools (from the authenticated JWT, via
    # config["configurable"]["tenant_id"]) directly into EnhancedContract
    # SearchTool._run's kwargs, bypassing this schema entirely - there is
    # no field here for the model to see, guess, or override.

class EnhancedContractSearchTool(BaseTool):
    name: str = "EnhancedContractSearch"
    description: str = (
        "Search uploaded contracts. Defaults to searching every level at once (document summary, "
        "sections, clauses, relationships, and the real verbatim chunk text), so it always has "
        "access to real contract content, not just a short AI-generated summary. For any question "
        "about what a contract actually says, do not restrict search_level - let it default."
    )
    args_schema: Type[BaseModel] = EnhancedContractInput

    def _run(
        self,
        tenant_id: str,
        search_level: SearchLevel = SearchLevel.ALL,
        clause_types: Optional[List[str]] = None,
        section_types: Optional[List[str]] = None,
        min_effective_date: Optional[str] = None,
        max_effective_date: Optional[str] = None,
        min_end_date: Optional[str] = None,
        max_end_date: Optional[str] = None,
        contract_type: Optional[str] = None,
        parties: Optional[List[str]] = None,
        summary_search: Optional[str] = None,
        active: Optional[bool] = None,
        monetary_value: Optional[MonetaryValue] = None,
        cypher_aggregation: Optional[str] = None,
        governing_law: Optional[Location] = None,
        # Deliberately NOT a field on EnhancedContractInput, same reasoning
        # as tenant_id above it in that class's docstring: the model has no
        # legitimate way to know the real selected contract_id, so it must
        # never be a guessable/spoofable tool-call argument. When the user
        # has a contract selected in the Chat UI, contract_chat_agent.py's
        # execute_tools injects the real one here from config[
        # "configurable"]["contract_id"] (itself only ever set server-side
        # from the authenticated request, in main.py's runner()).
        contract_id: Optional[str] = None,
    ) -> str:
        """Use the enhanced search tool"""
        return get_contracts_multi_level(
            embedding,
            tenant_id,
            search_level,
            clause_types,
            section_types,
            min_effective_date,
            max_effective_date,
            min_end_date,
            max_end_date,
            contract_type,
            parties,
            summary_search,
            active,
            cypher_aggregation,
            monetary_value,
            governing_law,
            contract_id,
        )
