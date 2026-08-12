from typing import Dict, Any
from backend.domain.search_entities import SearchResult

class SearchResponseMapper:
    """Maps search results to API responses (Single Responsibility)"""
    
    @staticmethod
    def to_api_response(result: SearchResult, search_level: str) -> Dict[str, Any]:
        """Convert SearchResult to standardized API response"""
        
        # Format results based on search level to match frontend expectations
        formatted_results = []
        if result.total_count > 0:
            if search_level == "document":
                formatted_results = [{"documents": result.items}]
            elif search_level == "section":
                formatted_results = [{"sections": result.items}]
            elif search_level == "clause":
                formatted_results = [{"clauses": result.items}]
            elif search_level == "relationship":
                formatted_results = [{"relationships": result.items}]
            elif search_level == "all":
                formatted_results = result.items  # Already formatted by service
        
        has_error = bool(result.search_metadata and result.search_metadata.get("error"))
        error_msg = result.search_metadata.get("error") if has_error else None

        response = {
            "success": not has_error,
            "search_level": search_level,
            "results": formatted_results,
            "contracts_found": result.total_count,
            "metadata": result.search_metadata
        }
        
        if has_error:
            response["error"] = error_msg
            response["message"] = f"Search error occurred: {error_msg}"
        elif result.total_count == 0:
            response["message"] = "No contracts found matching your search criteria."
            response["suggestions"] = [
                "Try removing some filters to broaden your search",
                "Use different search terms",
                "Check if contracts exist in the database"
            ]
        
        return response