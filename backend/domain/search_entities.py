from dataclasses import dataclass
from typing import Dict, List, Any, Optional
from enum import Enum

class SearchLevel(str, Enum):
    DOCUMENT = "document"
    SECTION = "section"
    CLAUSE = "clause"
    RELATIONSHIP = "relationship"
    ALL = "all"

@dataclass
class SearchParams:
    search_level: SearchLevel
    tenant_id: str
    query: Optional[str] = None
    clause_types: Optional[List[str]] = None
    section_types: Optional[List[str]] = None
    parties: Optional[List[str]] = None
    contract_type: Optional[str] = None
    active: Optional[bool] = None
    min_effective_date: Optional[str] = None
    max_effective_date: Optional[str] = None
    min_end_date: Optional[str] = None
    max_end_date: Optional[str] = None

@dataclass
class SearchResult:
    total_count: int
    items: List[Dict[str, Any]]
    search_metadata: Dict[str, Any]