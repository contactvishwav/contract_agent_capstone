from fastapi import APIRouter, HTTPException, Depends
from backend.governance.auth import TokenIdentity
from backend.governance.rbac import Permission, requires_permission
from typing import List, Optional
from pydantic import BaseModel, Field
from backend.domain.search_entities import SearchLevel, SearchParams
from backend.application.services.enhanced_search_service import EnhancedSearchService
from backend.shared.utils.search_mapper import SearchResponseMapper
import logging

from backend.shared.utils.logger import get_logger
logger = get_logger(__name__)

router = APIRouter(prefix="/contracts", tags=["Enhanced Contract Search"])

# Request models
class ClauseSearchRequest(BaseModel):
    clause_types: List[str] = Field(..., description="CUAD clause types to search")
    query: Optional[str] = Field(None, description="Semantic search query")
    limit: Optional[int] = Field(10, description="Maximum results to return")

class SectionSearchRequest(BaseModel):
    section_types: List[str] = Field(..., description="Section types to search")
    query: Optional[str] = Field(None, description="Semantic search query")
    limit: Optional[int] = Field(10, description="Maximum results to return")

class RelationshipSearchRequest(BaseModel):
    parties: Optional[List[str]] = Field(None, description="Party names to search")
    query: Optional[str] = Field(None, description="Semantic search query")
    limit: Optional[int] = Field(10, description="Maximum results to return")

class EnhancedSearchRequest(BaseModel):
    search_level: SearchLevel = Field(SearchLevel.DOCUMENT, description="Search level")
    query: Optional[str] = Field(None, description="Semantic search query")
    clause_types: Optional[List[str]] = Field(None, description="CUAD clause types")
    section_types: Optional[List[str]] = Field(None, description="Section types")
    parties: Optional[List[str]] = Field(None, description="Party names")
    
    # Date filters
    min_effective_date: Optional[str] = Field(None, description="Min effective date (YYYY-MM-DD)")
    max_effective_date: Optional[str] = Field(None, description="Max effective date (YYYY-MM-DD)")
    min_end_date: Optional[str] = Field(None, description="Min end date (YYYY-MM-DD)")
    max_end_date: Optional[str] = Field(None, description="Max end date (YYYY-MM-DD)")
    
    # Other filters
    contract_type: Optional[str] = Field(None, description="Contract type")
    active: Optional[bool] = Field(None, description="Active contracts only")


# Initialize the enhanced search service
search_service = EnhancedSearchService()

@router.post("/search/enhanced")
async def enhanced_contract_search(
    request: EnhancedSearchRequest,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Enhanced contract search with multi-level embedding support"""
    try:
        logger.info("\n=== ENHANCED SEARCH ===")
        logger.info(f"Search Level: {request.search_level}")
        logger.info(f"Query: {request.query}")
        logger.info(f"Contract Type: {request.contract_type}")
        logger.info(f"Active: {request.active}")

        # Convert request to search params. tenant_id comes exclusively from
        # the validated JWT (identity.tenant_id), never from the request
        # body - there is no tenant_id field on EnhancedSearchRequest at all,
        # so there is no way for a caller to smuggle a different tenant in.
        search_params = SearchParams(
            search_level=request.search_level,
            tenant_id=identity.tenant_id,
            query=request.query,
            clause_types=request.clause_types,
            section_types=request.section_types,
            parties=request.parties,
            contract_type=request.contract_type,
            active=request.active,
            min_effective_date=request.min_effective_date,
            max_effective_date=request.max_effective_date,
            min_end_date=request.min_end_date,
            max_end_date=request.max_end_date
        )
        
        # Execute search using service
        result = search_service.search(search_params)
        
        logger.info(f"Raw Search Result:")
        logger.info(f"  Total Count: {result.total_count}")
        logger.info(f"  Items Length: {len(result.items)}")
        logger.info(f"  First Item: {result.items[0] if result.items else 'None'}")
        logger.info(f"  Metadata: {result.search_metadata}")
        
        # Map to API response
        response = SearchResponseMapper.to_api_response(result, request.search_level.value)
        
        logger.info(f"Final API Response:")
        logger.info(f"  Success: {response['success']}")
        logger.info(f"  Contracts Found: {response['contracts_found']}")
        logger.info(f"  Results Length: {len(response['results'])}")
        logger.info(f"=== END SEARCH ===\n")
        
        return response
        
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Search failed: {str(e)}")

@router.post("/search/clauses")
async def search_clauses(
    request: ClauseSearchRequest,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Search contracts by specific clause types"""
    try:
        search_params = SearchParams(
            search_level=SearchLevel.CLAUSE,
            tenant_id=identity.tenant_id,
            query=request.query,
            clause_types=request.clause_types
        )
        result = search_service.search(search_params)
        # Was previously computed and discarded (no return at all) - this
        # route always sent the client an empty 200 response regardless of
        # what the search found. Found and fixed in passing while adding
        # tenant_id above; SearchResponseMapper is the same real mapper
        # /search/enhanced already uses, not a new formatting path.
        return SearchResponseMapper.to_api_response(result, "clause")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Clause search failed: {str(e)}")

@router.post("/search/sections")
async def search_sections(
    request: SectionSearchRequest,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Search contracts by document sections"""
    try:
        search_params = SearchParams(
            search_level=SearchLevel.SECTION,
            tenant_id=identity.tenant_id,
            query=request.query,
            section_types=request.section_types
        )
        result = search_service.search(search_params)
        return SearchResponseMapper.to_api_response(result, "section")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Section search failed: {str(e)}")

@router.post("/search/relationships")
async def search_relationships(
    request: RelationshipSearchRequest,
    identity: TokenIdentity = Depends(requires_permission(Permission.ANALYZE)),
):
    """Search contracts by party relationships"""
    try:
        search_params = SearchParams(
            search_level=SearchLevel.RELATIONSHIP,
            tenant_id=identity.tenant_id,
            query=request.query,
            parties=request.parties
        )
        result = search_service.search(search_params)
        return SearchResponseMapper.to_api_response(result, "relationship")
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"Relationship search failed: {str(e)}")

@router.get("/search/clause-types")
async def get_clause_types():
    """Get list of available CUAD clause types"""
    clause_types = [
        "Document Name", "Parties", "Agreement Date", "Effective Date", "Expiration Date",
        "Renewal Term", "Notice Period To Terminate Renewal", "Governing Law", 
        "Most Favored Nation", "Non-Compete", "Exclusivity", "No-Solicit Of Customers",
        "No-Solicit Of Employees", "Non-Disparagement", "Termination For Convenience",
        "Rofr/Rofo/Rofn", "Change Of Control", "Anti-Assignment", "Revenue/Customer Sharing",
        "Price Restrictions", "Minimum Commitment", "Volume Restriction", "IP Ownership Assignment",
        "Joint IP Ownership", "License Grant", "Non-Transferable License", "Affiliate License-Licensor",
        "Affiliate License-Licensee", "Unlimited/All-You-Can-Eat-License", "Irrevocable Or Perpetual License",
        "Source Code Escrow", "Post-Termination Services", "Audit Rights", "Uncapped Liability",
        "Cap On Liability", "Liquidated Damages", "Warranty Duration", "Insurance",
        "Covenant Not To Sue", "Third Party Beneficiary", "Escrow"
    ]
    
    return {
        "success": True,
        "clause_types": clause_types,
        "total_count": len(clause_types)
    }

@router.get("/search/section-types")
async def get_section_types():
    """Get list of available section types"""
    section_types = [
        {"value": "payment", "label": "Payment Terms", "description": "Payment schedules, fees, costs"},
        {"value": "termination", "label": "Termination", "description": "Contract ending conditions"},
        {"value": "liability", "label": "Liability", "description": "Damages, losses, limitations"},
        {"value": "intellectual_property", "label": "Intellectual Property", "description": "IP rights, patents, copyrights"},
        {"value": "confidentiality", "label": "Confidentiality", "description": "Non-disclosure, proprietary info"},
        {"value": "general", "label": "General", "description": "Other contract sections"}
    ]
    
    return {
        "success": True,
        "section_types": section_types,
        "total_count": len(section_types)
    }