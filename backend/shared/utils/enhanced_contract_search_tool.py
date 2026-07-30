from typing import Any, List, Optional, Type
from enum import Enum
from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from backend.shared.utils.gemini_embedding_service import embedding
from langchain_neo4j import Neo4jGraph
from pydantic import BaseModel, Field
from backend.shared.utils.vector_index_config import (
    CONTRACT_EMBEDDING_INDEX, SECTION_EMBEDDING_INDEX, CLAUSE_EMBEDDING_INDEX,
    CHUNK_EMBEDDING_INDEX, VECTOR_SEARCH_OVERFETCH,
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

graph: Neo4jGraph = Neo4jGraph(
    refresh_schema=False, driver_config={"notifications_min_severity": "OFF"}
)
# embedding imported from gemini_embedding_service (1536 dimensions)

def get_contracts_multi_level(
    embeddings: Any,
    tenant_id: str,
    search_level: SearchLevel = SearchLevel.DOCUMENT,
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
    governing_law: Optional[Location] = None
):
    """Enhanced contract search with multi-level embedding support and tenant isolation"""
    
    params: dict[str, Any] = {"tenant_id": tenant_id}
    filters: list[str] = ["c.tenant_id = $tenant_id"]
    
    if search_level == SearchLevel.DOCUMENT:
        return _search_documents(embeddings, tenant_id, summary_search, filters, params, 
                               min_effective_date, max_effective_date, min_end_date, max_end_date,
                               contract_type, parties, active, cypher_aggregation, monetary_value, governing_law)
    
    elif search_level == SearchLevel.SECTION:
        return _search_sections(embeddings, tenant_id, summary_search, section_types, filters, params)
    
    elif search_level == SearchLevel.CLAUSE:
        return _search_clauses(embeddings, tenant_id, summary_search, clause_types, filters, params)
    
    elif search_level == SearchLevel.RELATIONSHIP:
        return _search_relationships(embeddings, tenant_id, summary_search, parties, filters, params)
    
    elif search_level == SearchLevel.CHUNK:
        return _search_chunks(embeddings, tenant_id, summary_search, filters, params)
    
    elif search_level == SearchLevel.ALL:
        return _search_all_levels(embeddings, tenant_id, summary_search, clause_types, section_types, 
                                 filters, params)

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

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        params["k"] = VECTOR_SEARCH_OVERFETCH

        # Query the vector index for candidates (instead of scoring every
        # Contract node), then apply the same non-vector filters afterward -
        # a vector index query has no way to pre-filter by an arbitrary
        # property before ranking globally.
        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{CONTRACT_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS c, score AS doc_score
        WHERE doc_score > 0.8
        """
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
        cypher_statement += "ORDER BY doc_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

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
        })[..10]
    } AS result
    """
    
    output = graph.query(cypher_statement, params)
    return [convert_neo4j_date(el) for el in output]

def _search_sections(embeddings, tenant_id, summary_search, section_types, filters, params):
    """Search at section level with tenant isolation"""
    # Add tenant filtering (Contract node has tenant_id)
    filters.append("c.tenant_id = $tenant_id")

    if section_types:
        filters.append("s.section_type IN $section_types")
        params["section_types"] = section_types

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        params["k"] = VECTOR_SEARCH_OVERFETCH

        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{SECTION_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS s, score AS section_score
        MATCH (c:Contract)-[:HAS_SECTION]->(s)
        WHERE section_score > 0.8
        """
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
        cypher_statement += "ORDER BY section_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract)-[:HAS_SECTION]->(s:Section) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

    cypher_statement += """
    RETURN {
        total_count: count(s),
        sections: collect({
            contract_id: c.file_id,
            section_type: s.section_type,
            content: s.content,
            order: s.order
        })[..10]
    } AS result
    """
    
    output = graph.query(cypher_statement, params)
    return [convert_neo4j_date(el) for el in output]

def _search_clauses(embeddings, tenant_id, summary_search, clause_types, filters, params):
    """Search at clause level with tenant isolation"""
    # Add tenant filtering
    filters.append("c.tenant_id = $tenant_id")

    if clause_types:
        filters.append("cl.clause_type IN $clause_types")
        params["clause_types"] = clause_types

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        params["k"] = VECTOR_SEARCH_OVERFETCH

        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{CLAUSE_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS cl, score AS clause_score
        MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl)
        WHERE clause_score > 0.8
        """
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
        cypher_statement += "ORDER BY clause_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract)-[:CONTAINS_CLAUSE]->(cl:Clause) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

    cypher_statement += """
    RETURN {
        total_count: count(cl),
        clauses: collect({
            contract_id: c.file_id,
            clause_type: cl.clause_type,
            content: cl.content,
            confidence: cl.confidence,
            start_position: cl.start_position,
            end_position: cl.end_position
        })[..10]
    } AS result
    """
    
    output = graph.query(cypher_statement, params)
    return [convert_neo4j_date(el) for el in output]

def _search_relationships(embeddings, tenant_id, summary_search, parties, filters, params):
    """Search at relationship level with tenant isolation"""
    cypher_statement = "MATCH (c:Contract)<-[r:PARTY_TO]-(p:Party) "
    
    # Add tenant filtering
    filters.append("c.tenant_id = $tenant_id")
    
    if parties:
        filters.append("p.name IN $parties")
        params["parties"] = parties
    
    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        
        cypher_statement += """
        WHERE r.embedding IS NOT NULL AND vector.similarity.cosine(r.embedding, $summary_embedding) > 0.8
        """
        
        if filters:
            cypher_statement += f"AND {' AND '.join(filters)} "
    elif filters:
        cypher_statement += f"WHERE {' AND '.join(filters)} "
    
    cypher_statement += """
    RETURN {
        total_count: count(r),
        relationships: collect({
            contract_id: c.file_id,
            party_name: p.name,
            role: r.role,
            context: r.context
        })[..10]
    } AS result
    """
    
    output = graph.query(cypher_statement, params)
    return [convert_neo4j_date(el) for el in output]

def _search_chunks(embeddings, tenant_id, summary_search, filters, params):
    """Enhanced search at chunk level with semantic capabilities and tenant isolation"""
    
    # Try semantic search first if available
    if summary_search:
        try:
            # Query the vector index for candidates (instead of scoring
            # every Chunk node), then enforce tenant scoping afterward - a
            # vector index query has no way to pre-filter by tenant_id
            # before ranking globally.
            semantic_query = f"""
            CALL db.index.vector.queryNodes('{CHUNK_EMBEDDING_INDEX}', $k, $chunk_embedding)
            YIELD node AS c, score AS chunk_score
            MATCH (d:Document)-[:HAS_CHUNK]->(c)
            WHERE d.tenant_id = $tenant_id AND chunk_score > 0.7
            RETURN {{
                total_count: count(c),
                chunks: collect({{
                    document_id: d.id,
                    chunk_type: c.chunk_type,
                    content: substring(c.content, 0, 200) + '...',
                    chunk_index: c.chunk_index,
                    quality_score: c.quality_score,
                    similarity_score: chunk_score,
                    search_type: 'semantic'
                }})[..10]
            }} AS result
            ORDER BY chunk_score DESC
            """

            chunk_embedding = embeddings.embed_query(summary_search)
            semantic_params = {"chunk_embedding": chunk_embedding, "tenant_id": tenant_id, "k": VECTOR_SEARCH_OVERFETCH}
            
            semantic_output = graph.query(semantic_query, semantic_params)
            if semantic_output and semantic_output[0]['result']['total_count'] > 0:
                return [convert_neo4j_date(el) for el in semantic_output]
        except Exception as e:
            logger.error(f"Semantic chunk search failed, falling back to text search: {e}")
    
    # Fallback to text search across both new and legacy chunks, enforcing tenant_id
    cypher_statement = """
    MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
    WHERE c.content CONTAINS $search_text AND d.tenant_id = $tenant_id
    RETURN {
        total_count: count(c),
        chunks: collect({
            document_id: d.id,
            chunk_type: c.chunk_type,
            content: substring(c.content, 0, 200) + '...',
            chunk_index: c.chunk_index,
            quality_score: c.quality_score,
            search_type: 'text_new'
        })[..5]
    } AS result
    
    UNION
    
    MATCH (c:Contract)-[:CONTAINS_CHUNK]->(dc:DocumentChunk)
    WHERE dc.content CONTAINS $search_text AND c.tenant_id = $tenant_id
    RETURN {
        total_count: count(dc),
        chunks: collect({
            contract_id: c.file_id,
            chunk_type: dc.chunk_type,
            content: substring(dc.content, 0, 200) + '...',
            chunk_order: dc.chunk_order,
            confidence: dc.confidence,
            search_type: 'text_legacy'
        })[..5]
    } AS result
    """
    
    if summary_search:
        params["search_text"] = summary_search
    else:
        # If no search text, return recent chunks for current tenant
        cypher_statement = """
        MATCH (d:Document)-[:HAS_CHUNK]->(c:Chunk)
        WHERE d.tenant_id = $tenant_id
        RETURN {
            total_count: count(c),
            chunks: collect({
                document_id: d.id,
                chunk_type: c.chunk_type,
                content: substring(c.content, 0, 200) + '...',
                chunk_index: c.chunk_index,
                quality_score: c.quality_score,
                search_type: 'recent'
            })[..10]
        } AS result
        ORDER BY c.chunk_index DESC
        """
    
    output = graph.query(cypher_statement, params)
    return [convert_neo4j_date(el) for el in output]

def _search_all_levels(embeddings, tenant_id, summary_search, clause_types, section_types, filters, params):
    """Search across all levels and combine results with tenant isolation"""
    results = {
        "documents": _search_documents(embeddings, tenant_id, summary_search, ["c.tenant_id = $tenant_id"], {"tenant_id": tenant_id}, None, None, None, None, None, None, None, None, None, None),
        "sections": _search_sections(embeddings, tenant_id, summary_search, section_types, ["c.tenant_id = $tenant_id"], {"tenant_id": tenant_id}),
        "clauses": _search_clauses(embeddings, tenant_id, summary_search, clause_types, ["c.tenant_id = $tenant_id"], {"tenant_id": tenant_id}),
        "relationships": _search_relationships(embeddings, tenant_id, summary_search, None, ["c.tenant_id = $tenant_id"], {"tenant_id": tenant_id}),
        "chunks": _search_chunks(embeddings, tenant_id, summary_search, ["c.tenant_id = $tenant_id"], {"tenant_id": tenant_id})
    }
    return [results]

class EnhancedContractInput(BaseModel):
    search_level: Optional[SearchLevel] = Field(SearchLevel.DOCUMENT, description="Level of search: document, section, clause, relationship, or all")
    clause_types: Optional[List[str]] = Field(None, description="Specific CUAD clause types to search")
    section_types: Optional[List[str]] = Field(None, description="Document sections to focus on: payment, termination, liability, etc.")
    
    # Existing fields
    min_effective_date: Optional[str] = Field(None, description="Earliest contract effective date (YYYY-MM-DD)")
    max_effective_date: Optional[str] = Field(None, description="Latest contract effective date (YYYY-MM-DD)")
    min_end_date: Optional[str] = Field(None, description="Earliest contract end date (YYYY-MM-DD)")
    max_end_date: Optional[str] = Field(None, description="Latest contract end date (YYYY-MM-DD)")
    contract_type: Optional[str] = Field(None, description="Contract type")
    parties: Optional[List[str]] = Field(None, description="List of parties involved in the contract")
    summary_search: Optional[str] = Field(None, description="Semantic search of contract content")
    active: Optional[bool] = Field(None, description="Whether the contract is active")
    governing_law: Optional[Location] = Field(None, description="Governing law of the contract")
    monetary_value: Optional[MonetaryValue] = Field(None, description="The total amount or value of a contract")
    cypher_aggregation: Optional[str] = Field(None, description="Custom Cypher statement for advanced aggregations")
    tenant_id: str = Field(..., description="The ID of the tenant requesting the search")

class EnhancedContractSearchTool(BaseTool):
    name: str = "EnhancedContractSearch"
    description: str = (
        "Advanced contract search with multi-level embedding support. "
        "Can search at document, section, clause, chunk, or relationship levels for precise results."
    )
    args_schema: Type[BaseModel] = EnhancedContractInput

    def _run(
        self,
        tenant_id: str,
        search_level: SearchLevel = SearchLevel.DOCUMENT,
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
        governing_law: Optional[Location] = None
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
            governing_law
        )