import React, { useState, useCallback } from 'react';
import { EnhancedSearchInterface, EnhancedSearchParams } from '../components/features/search/EnhancedSearchInterface';
import { EnhancedSearchResults } from '../components/features/search/EnhancedSearchResults';
import { enhancedSearchApi } from '../services/enhancedSearchApi';

interface SearchState {
  // EnhancedSearchResults (see below) genuinely accepts either an array
  // of results or a single result object and normalizes at runtime
  // (Array.isArray check) - handleSearch always stores an array (see its
  // own results-shape handling below), so that's what this reflects.
  results: any[] | null;
  isLoading: boolean;
  error: string | null;
}

export const SearchPage: React.FC = () => {
  const [searchState, setSearchState] = useState<SearchState>({
    results: null,
    isLoading: false,
    error: null
  });

  const [lastSearchParams, setLastSearchParams] = useState<EnhancedSearchParams | null>(null);

  const handleSearch = useCallback(async (searchParams: EnhancedSearchParams) => {
    setSearchState(prev => ({ ...prev, isLoading: true, error: null }));
    setLastSearchParams(searchParams);

    try {
      const apiResponse = await enhancedSearchApi.searchContracts(searchParams);

      if (apiResponse && (apiResponse.success === false || (apiResponse as any).error)) {
        const errorMessage = (apiResponse as any).error || (apiResponse as any).message || 'Search failed on backend';
        setSearchState({ results: null, isLoading: false, error: errorMessage });
        return;
      }

      // Handle different response structures
      let results;
      if (Array.isArray(apiResponse)) {
        results = apiResponse;
      } else if (apiResponse && typeof apiResponse === 'object') {
        // If response has a results property, use it
        results = apiResponse.results || [apiResponse];
      } else {
        results = [];
      }

      setSearchState({ results, isLoading: false, error: null });
    } catch (err) {
      const errorMessage = err instanceof Error ? err.message : 'Search failed';
      setSearchState({ results: null, isLoading: false, error: errorMessage });
    }
  }, []);

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="text-center bg-white rounded-lg p-6 shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold text-slate-800 mb-2">Enhanced Contract Search</h1>
        <p className="text-lg text-slate-600">
          Multi-level semantic search across documents, sections, clauses, and relationships
        </p>
      </div>

      {/* Search Interface */}
      <EnhancedSearchInterface
        onSearch={handleSearch}
        isLoading={searchState.isLoading}
      />

      {/* Error Display */}
      {searchState.error && (
        <div className="bg-red-50 border border-red-200 rounded-lg p-4" role="alert">
          <p className="text-red-700">Error: {searchState.error}</p>
        </div>
      )}

      {/* Search Results */}
      {searchState.results && lastSearchParams && (
        <EnhancedSearchResults
          results={searchState.results}
          searchLevel={lastSearchParams.searchLevel}
        />
      )}
    </div>
  );
};