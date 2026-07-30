from abc import ABC, abstractmethod
from typing import Any, List, Dict
from backend.domain.search_entities import SearchParams, SearchResult
from backend.shared.utils.contract_search_tool import graph, embedding
from backend.shared.utils.utils import convert_neo4j_date
from backend.shared.utils.vector_index_config import (
    CONTRACT_EMBEDDING_INDEX, CLAUSE_EMBEDDING_INDEX, SECTION_EMBEDDING_INDEX,
    VECTOR_SEARCH_OVERFETCH,
)

class SearchStrategy(ABC):
    """Abstract base class for search strategies (Strategy Pattern)"""
    
    @abstractmethod
    def execute(self, params: SearchParams) -> SearchResult:
        pass

class DocumentSearchStrategy(SearchStrategy):
    """Document-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {}
            filters = []

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
            
            output = graph.query(cypher_statement, cypher_params)
            
            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                contracts = [convert_neo4j_date(contract) for contract in result_data.get("contracts", [])]
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=contracts,
                    search_metadata={"search_level": "document", "query": params.query}
                )
            
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "document"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "document", "error": str(e)})

class ClauseSearchStrategy(SearchStrategy):
    """Clause-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {}
            filters = []

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

            cypher_statement += """
            RETURN {
                total_count: count(cl),
                clauses: collect({
                    contract_id: c.file_id,
                    clause_type: cl.clause_type,
                    content: cl.content,
                    confidence: cl.confidence
                })[..10]
            } AS result
            """
            
            output = graph.query(cypher_statement, cypher_params)
            
            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                clauses = [convert_neo4j_date(clause) for clause in result_data.get("clauses", [])]
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=clauses,
                    search_metadata={"search_level": "clause", "clause_types": params.clause_types}
                )
            
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "clause"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "clause", "error": str(e)})

class SectionSearchStrategy(SearchStrategy):
    """Section-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {}
            filters = []

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
            
            output = graph.query(cypher_statement, cypher_params)
            
            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                sections = [convert_neo4j_date(section) for section in result_data.get("sections", [])]
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=sections,
                    search_metadata={"search_level": "section", "section_types": params.section_types}
                )
            
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "section"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "section", "error": str(e)})

class RelationshipSearchStrategy(SearchStrategy):
    """Relationship-level search implementation"""
    
    def execute(self, params: SearchParams) -> SearchResult:
        try:
            cypher_params = {}
            filters = []
            
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
            
            output = graph.query(cypher_statement, cypher_params)
            
            if output and len(output) > 0 and "result" in output[0]:
                result_data = output[0]["result"]
                relationships = [convert_neo4j_date(rel) for rel in result_data.get("relationships", [])]
                return SearchResult(
                    total_count=result_data.get("total_count", 0),
                    items=relationships,
                    search_metadata={"search_level": "relationship", "parties": params.parties}
                )
            
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "relationship"})
            
        except Exception as e:
            return SearchResult(total_count=0, items=[], search_metadata={"search_level": "relationship", "error": str(e)})