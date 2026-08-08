import hashlib
import json
from typing import Dict
from backend.domain.search_entities import SearchLevel, SearchParams, SearchResult
from backend.shared.cache.redis_cache import cache
from backend.shared.config.phase3_config import Phase3Config
from backend.shared.utils.search_strategies import (
    SearchStrategy,
    DocumentSearchStrategy,
    ClauseSearchStrategy,
    SectionSearchStrategy,
    RelationshipSearchStrategy
)


def _search_cache_key(params: SearchParams) -> str:
    """
    Tenant-scoped (tenant_id is one of the hashed fields, same as every
    other field affecting the result) - mirrors enhanced_contract_search_
    tool.py's _multi_level_search_cache_key's identical rationale for the
    other (LangChain-tool-facing) search path, applied here for the real
    REST API path, which had no caching at all before this. Includes
    RERANKING_ENABLED so flipping that flag never serves a cached result
    computed under the other setting.
    """
    key_data = {
        "search_level": params.search_level.value,
        "tenant_id": params.tenant_id,
        "query": params.query,
        "clause_types": sorted(params.clause_types) if params.clause_types else None,
        "section_types": sorted(params.section_types) if params.section_types else None,
        "parties": sorted(params.parties) if params.parties else None,
        "contract_type": params.contract_type,
        "active": params.active,
        "min_effective_date": params.min_effective_date,
        "max_effective_date": params.max_effective_date,
        "min_end_date": params.min_end_date,
        "max_end_date": params.max_end_date,
        "reranking_enabled": Phase3Config.RERANKING_ENABLED,
    }
    raw = f"vector_search:enhanced_search:{json.dumps(key_data, sort_keys=True, default=str)}"
    return f"vector_search:{params.tenant_id}:rest:{hashlib.sha256(raw.encode()).hexdigest()}"


class EnhancedSearchService:
    """Unified search service using Dependency Inversion principle"""

    def __init__(self):
        self._strategies: Dict[SearchLevel, SearchStrategy] = {
            SearchLevel.DOCUMENT: DocumentSearchStrategy(),
            SearchLevel.CLAUSE: ClauseSearchStrategy(),
            SearchLevel.SECTION: SectionSearchStrategy(),
            SearchLevel.RELATIONSHIP: RelationshipSearchStrategy()
        }

    def search(self, params: SearchParams) -> SearchResult:
        """
        Execute search using appropriate strategy. Cached the same way
        the vector-search path is cached everywhere else in this codebase -
        same TTL bucket ("vector_search"), so a repeated identical query
        (including its re-ranked order, when reranking is on) doesn't pay
        the re-ranking LLM cost again within the TTL window.
        """
        cache_key = _search_cache_key(params)
        if Phase3Config.CACHE_ENABLED:
            cached = cache.get(cache_key)
            if cached is not None:
                return SearchResult(**cached)

        if params.search_level == SearchLevel.ALL:
            result = self._search_all_levels(params)
        else:
            strategy = self._strategies.get(params.search_level)
            if not strategy:
                raise ValueError(f"Unsupported search level: {params.search_level}")
            result = strategy.execute(params)

        # Never cache a transient failure - each strategy's own except block
        # degrades to search_metadata={"error": ...} rather than raising, so
        # this is the only place that can distinguish "genuinely no
        # results" from "the query blew up" before it goes in the cache.
        # Caching the latter would hide a real subsequent success behind a
        # cached error for the whole TTL window.
        if Phase3Config.CACHE_ENABLED and "error" not in result.search_metadata:
            cache.set(
                cache_key,
                {"total_count": result.total_count, "items": result.items, "search_metadata": result.search_metadata},
                ttl=Phase3Config.get_cache_ttl("vector_search"),
            )
        return result
    
    def _search_all_levels(self, params: SearchParams) -> SearchResult:
        """Combine results from all search levels"""
        all_results = {}
        total_count = 0
        
        for level, strategy in self._strategies.items():
            if level != SearchLevel.ALL:
                level_params = SearchParams(
                    search_level=level,
                    tenant_id=params.tenant_id,
                    query=params.query,
                    clause_types=params.clause_types if level == SearchLevel.CLAUSE else None,
                    section_types=params.section_types if level == SearchLevel.SECTION else None,
                    parties=params.parties if level == SearchLevel.RELATIONSHIP else None
                )
                result = strategy.execute(level_params)
                all_results[level.value] = result.items
                total_count += result.total_count
        
        return SearchResult(
            total_count=total_count,
            items=[all_results],
            search_metadata={"search_level": "all", "query": params.query}
        )
