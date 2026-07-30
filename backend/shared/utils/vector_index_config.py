"""
Shared registry of Neo4j vector index names, so the migration that creates
them (backend/migrations/vector_index_migration.py) and every query call
site that uses them (backend/shared/utils/contract_search_tool.py,
search_strategies.py, enhanced_contract_search_tool.py, backend/
infrastructure/embedding_service.py, policy_repository.py, backend/
infrastructure/chunking/chunk_embedding_service.py) stay in sync on the
same names, instead of each hardcoding its own string.
"""

# Gemini's embedding model (gemini-embedding-001) output dimension - see
# backend/embeddings/validator.py and backend/shared/utils/
# gemini_embedding_service.py.
EMBEDDING_DIMENSIONS = 1536

# {index_name: (node_label, embedding_property)}
VECTOR_INDEXES = {
    "contract_embedding_vector_index": ("Contract", "embedding"),
    "section_embedding_vector_index": ("Section", "embedding"),
    "clause_embedding_vector_index": ("Clause", "embedding"),
    "chunk_embedding_vector_index": ("Chunk", "embedding"),
    "policy_document_embedding_vector_index": ("PolicyDocument", "embedding"),
}

CONTRACT_EMBEDDING_INDEX = "contract_embedding_vector_index"
SECTION_EMBEDDING_INDEX = "section_embedding_vector_index"
CLAUSE_EMBEDDING_INDEX = "clause_embedding_vector_index"
CHUNK_EMBEDDING_INDEX = "chunk_embedding_vector_index"
POLICY_DOCUMENT_EMBEDDING_INDEX = "policy_document_embedding_vector_index"

# How many nearest-neighbor candidates to request from the vector index
# before any additional (tenant_id, contract_type, date-range, etc.) filter
# is applied in the same query. A vector index query returns its top-K by
# similarity GLOBALLY across every indexed node - it has no way to pre-filter
# by an arbitrary property before ranking. Requesting only the caller's
# final desired count (e.g. 10) and filtering afterward risks losing
# genuinely-relevant results that rank outside the global top 10 for reasons
# unrelated to relevance (e.g. another tenant's higher-scoring documents).
# Over-fetching a larger candidate pool and filtering that keeps this
# correct for a small-to-medium multi-tenant corpus, at a small cost to the
# latency win vs. true brute force - this is a known, standard tradeoff for
# ANN search under post-hoc filtering, not a bug. If a tenant's true match
# count for a given label ever regularly exceeds this, the fix is a bigger
# over-fetch, not per-tenant indexes.
VECTOR_SEARCH_OVERFETCH = 200
