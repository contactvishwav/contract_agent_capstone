from typing import Any, List, Optional, Type

from dotenv import load_dotenv
from langchain_core.tools import BaseTool
from backend.shared.utils.gemini_embedding_service import embedding
from langchain_neo4j import Neo4jGraph
from pydantic import BaseModel, Field
from enum import Enum
from backend.shared.utils.vector_index_config import CONTRACT_EMBEDDING_INDEX, VECTOR_SEARCH_OVERFETCH

load_dotenv()


from .utils import convert_neo4j_date

CONTRACT_TYPES = [
    "Affiliate Agreement",
    "Development",
    "Distributor", 
    "Endorsement",
    "Franchise",
    "Hosting",
    "IP",
    "Joint Venture",
    "License Agreement",
    "Maintenance",
    "Manufacturing",
    "Marketing",
    "Non Compete/Solicit",
    "Outsourcing",
    "Promotion",
    "Reseller",
    "Service",
    "Sponsorship",
    "Strategic Alliance",
    "Supply",
    "Transportation",
    # New contract types
    "MSA",
    "Master Services Agreement",
    "SOW", 
    "Statement of Work",
    "NDA",
    "MNDA",
    "Non-Disclosure Agreement",
    "DPA",
    "Data Processing Agreement",
    "SaaS Agreement",
    "Subscription Agreement",
    "IP Addendum",
    "Licensing Addendum",
]

graph: Neo4jGraph = Neo4jGraph(
    refresh_schema=False, driver_config={"notifications_min_severity": "OFF"}
)
# embedding imported from gemini_embedding_service (1536 dimensions)


class NumberOperator(str, Enum):
    EQUALS = "="
    GREATER_THAN = ">"
    LESS_THAN = "<"


class MonetaryValue(BaseModel):
    """The total amount or value of a contract"""

    value: float
    operator: NumberOperator

class Location(BaseModel):
    """Specified location"""

    country: Optional[str] = Field(None, description="Use two-letter ISO standard")
    state: Optional[str]


def get_contracts(
    embeddings: Any,
    tenant_id: str,
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
    params: dict[str, Any] = {"tenant_id": tenant_id}
    filters: list[str] = [
        "c.tenant_id = $tenant_id",
        "coalesce(c.lifecycle_status, 'ACTIVE') = 'ACTIVE'",
    ]

    if governing_law:
        if governing_law.country:
            filters.append(
            """EXISTS {
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

    if min_end_date:
        filters.append("c.end_date >= date($min_end_date)")
        params["min_end_date"] = min_end_date

    if max_end_date:
        filters.append("c.end_date <= date($max_end_date)")
        params["max_end_date"] = max_end_date

    if contract_type:
        filters.append("c.contract_type = $contract_type")
        params["contract_type"] = contract_type

    if parties:
        filters.append("""ANY(party_name IN $parties WHERE EXISTS {
            MATCH (c)<-[:PARTY_TO]-(p:Party)
            WHERE p.name = party_name
        })""")
        params["parties"] = parties

    if active is not None:
        operator = ">=" if active else "<"
        filters.append(f"c.end_date {operator} date()")

    if summary_search:
        summary_embedding = embeddings.embed_query(summary_search)
        params["summary_embedding"] = summary_embedding
        params["k"] = VECTOR_SEARCH_OVERFETCH

        # Query the vector index for the top-K candidates by similarity
        # (instead of scoring every Contract node), then apply the same
        # non-vector filters (including the mandatory tenant_id scope)
        # afterward - a vector index query has no way to pre-filter by an
        # arbitrary property before ranking globally.
        cypher_statement = f"""
        CALL db.index.vector.queryNodes('{CONTRACT_EMBEDDING_INDEX}', $k, $summary_embedding)
        YIELD node AS c, score AS doc_score
        """

        # Determine search type based on query tokens
        query_tokens = summary_search.upper().split()
        exact_match_type = next((t for t in query_tokens if t in CONTRACT_TYPES), None)

        if exact_match_type:
            # Boost exact type matches
            params["exact_type"] = exact_match_type
            all_filters = filters + ["(c.contract_type = $exact_type OR doc_score > 0.8)"]
            cypher_statement += f"WHERE {' AND '.join(all_filters)} "
            cypher_statement += "ORDER BY c.contract_type = $exact_type DESC, doc_score DESC "
        else:
            # Pure semantic search
            all_filters = filters + ["doc_score > 0.8"]
            cypher_statement += f"WHERE {' AND '.join(all_filters)} "
            cypher_statement += "ORDER BY doc_score DESC "
    else:
        cypher_statement = "MATCH (c:Contract) "
        if filters:
            cypher_statement += f"WHERE {' AND '.join(filters)} "

    if cypher_aggregation:
        cypher_statement += f"\n {cypher_aggregation}"
    else:
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


class ContractInput(BaseModel):
    min_effective_date: Optional[str] = Field(
        None, description="Earliest contract effective date (YYYY-MM-DD)"
    )
    max_effective_date: Optional[str] = Field(
        None, description="Latest contract effective date (YYYY-MM-DD)"
    )
    min_end_date: Optional[str] = Field(
        None, description="Earliest contract end date (YYYY-MM-DD)"
    )
    max_end_date: Optional[str] = Field(
        None, description="Latest contract end date (YYYY-MM-DD)"
    )
    contract_type: Optional[str] = Field(None, description="Contract type")
    parties: Optional[List[str]] = Field(
        None, description="List of parties involved in the contract"
    )
    summary_search: Optional[str] = Field(
        None, description="Semantic search of contract content"
    )
    active: Optional[bool] = Field(None, description="Whether the contract is active")
    governing_law: Optional[Location] = Field(None, description="Governing law of the contract")
    monetary_value: Optional[MonetaryValue] = Field(
        None, description="The total amount or value of a contract"
    )
    cypher_aggregation: Optional[str] = Field(
        None,
        description="""Custom Cypher statement for advanced aggregations and analytics.""",
    )

    # tenant_id is deliberately NOT a field here - see EnhancedContractInput's
    # identical comment in enhanced_contract_search_tool.py for the full
    # rationale. The real tenant_id is injected server-side by contract_
    # chat_agent.py's execute_tools directly into ContractSearchTool._run's
    # kwargs; the LLM never sees or supplies it.


class ContractSearchTool(BaseTool):
    name: str = "ContractSearch"
    description: str = (
        "useful for when you need to answer questions related to any contracts. Uses hybrid search: exact matching for common types (MSA, SOW, NDA) and semantic search for others."
    )
    args_schema: Type[BaseModel] = ContractInput

    def _run(
        self,
        tenant_id: str,
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
        """Use the tool."""
        return get_contracts(
            embedding,
            tenant_id,
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
