import React from 'react';
import { FileText, Hash, Users } from 'lucide-react';

interface SearchResult {
  documents?: DocumentResult[];
  sections?: SectionResult[];
  clauses?: ClauseResult[];
  relationships?: RelationshipResult[];
}

interface DocumentResult {
  file_id: string;
  summary: string;
  contract_type: string;
  effective_date: string;
  end_date: string;
  parties: Array<{ name: string; role: string }>;
}

interface SectionResult {
  contract_id: string;
  section_type: string;
  content: string;
  order: number;
}

interface ClauseResult {
  contract_id: string;
  clause_type: string;
  content: string;
  confidence: number;
  start_position: number;
  end_position: number;
}

interface RelationshipResult {
  contract_id: string;
  party_name: string;
  role: string;
  context: string;
}

interface EnhancedSearchResultsProps {
  results: any;
  searchLevel: string;
  totalCount?: number;
  className?: string;
}

export const EnhancedSearchResults: React.FC<EnhancedSearchResultsProps> = ({
  results,
  searchLevel,
  totalCount,
  className = ''
}) => {
  // Handle different result structures
  let processedResults: SearchResult[] = [];

  if (!results) {
    return (
      <div className={`text-center py-8 text-slate-500 ${className}`}>
        No results found. Try adjusting your search criteria.
      </div>
    );
  }

  // removed console logs

  // Check if results object contains a backend error
  if (results && typeof results === 'object' && !Array.isArray(results) && (results.error || results.success === false)) {
    const errorMsg = results.error || results.message || 'Search error occurred';
    return (
      <div className={`p-4 bg-red-50 border border-red-200 rounded-lg text-red-700 ${className}`} role="alert">
        <p className="font-semibold">Search Error</p>
        <p className="text-sm mt-1">{errorMsg}</p>
      </div>
    );
  }

  // Convert results to expected format
  if (Array.isArray(results)) {
    processedResults = results;
  } else if (typeof results === 'object') {
    processedResults = [results];
  }

  const hasItems = processedResults.some((res) => {
    if (!res) return false;
    if (searchLevel === 'document') return Boolean(res.documents && res.documents.length > 0);
    if (searchLevel === 'section') return Boolean(res.sections && res.sections.length > 0);
    if (searchLevel === 'clause') return Boolean(res.clauses && res.clauses.length > 0);
    if (searchLevel === 'relationship') return Boolean(res.relationships && res.relationships.length > 0);
    if (searchLevel === 'all') {
      return Boolean(
        (res.documents && res.documents.length > 0) ||
        (res.sections && res.sections.length > 0) ||
        (res.clauses && res.clauses.length > 0) ||
        (res.relationships && res.relationships.length > 0)
      );
    }
    return false;
  });

  if (processedResults.length === 0 || !hasItems) {
    return (
      <div className={`text-center py-8 text-slate-500 bg-white rounded-lg border border-slate-200 p-6 ${className}`}>
        No results found. Try adjusting your search criteria.
      </div>
    );
  }

  const renderDocumentResults = (documents: DocumentResult[]) => (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-lg font-semibold text-slate-800">
        <FileText className="w-5 h-5" />
        Document Results ({documents.length})
      </h3>
      {documents.map((doc, idx) => (
        <div key={idx} className="border border-slate-200 rounded-lg p-4 hover:shadow-md transition-shadow">
          <div className="flex justify-between items-start mb-2">
            <h4 className="font-medium text-slate-800">{doc.file_id}</h4>
            <span className="px-2 py-1 bg-blue-100 text-blue-800 text-xs rounded">{doc.contract_type}</span>
          </div>
          <p className="text-sm text-slate-600 mb-3">{doc.summary}</p>
          <div className="flex flex-wrap gap-4 text-xs text-slate-500">
            <span>Effective: {doc.effective_date}</span>
            <span>End: {doc.end_date}</span>
            <span>Parties: {doc.parties?.map(p => p.name).join(', ')}</span>
          </div>
        </div>
      ))}
    </div>
  );

  const renderSectionResults = (sections: SectionResult[]) => (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-lg font-semibold text-slate-800">
        <Hash className="w-5 h-5" />
        Section Results ({sections.length})
      </h3>
      {sections.map((section, idx) => (
        <div key={idx} className="border border-slate-200 rounded-lg p-4 hover:shadow-md transition-shadow">
          <div className="flex justify-between items-start mb-2">
            <h4 className="font-medium text-slate-800">{section.contract_id}</h4>
            <span className="px-2 py-1 bg-green-100 text-green-800 text-xs rounded">{section.section_type}</span>
          </div>
          <p className="text-sm text-slate-600">{section.content}</p>
          <div className="mt-2 text-xs text-slate-500">
            Section Order: {section.order}
          </div>
        </div>
      ))}
    </div>
  );

  const renderClauseResults = (clauses: ClauseResult[]) => (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-lg font-semibold text-slate-800">
        <FileText className="w-5 h-5" />
        Clause Results ({clauses.length})
      </h3>
      {clauses.map((clause, idx) => (
        <div key={idx} className="border border-slate-200 rounded-lg p-4 hover:shadow-md transition-shadow">
          <div className="flex justify-between items-start mb-2">
            <h4 className="font-medium text-slate-800">{clause.contract_id}</h4>
            <div className="flex gap-2">
              <span className="px-2 py-1 bg-purple-100 text-purple-800 text-xs rounded">{clause.clause_type}</span>
              <span className="px-2 py-1 bg-slate-100 text-slate-700 text-xs rounded">
                {Math.round(clause.confidence * 100)}% confidence
              </span>
            </div>
          </div>
          <p className="text-sm text-slate-600">{clause.content}</p>
          <div className="mt-2 text-xs text-slate-500">
            Position: {clause.start_position} - {clause.end_position}
          </div>
        </div>
      ))}
    </div>
  );

  const renderRelationshipResults = (relationships: RelationshipResult[]) => (
    <div className="space-y-4">
      <h3 className="flex items-center gap-2 text-lg font-semibold text-slate-800">
        <Users className="w-5 h-5" />
        Relationship Results ({relationships.length})
      </h3>
      {relationships.map((rel, idx) => (
        <div key={idx} className="border border-slate-200 rounded-lg p-4 hover:shadow-md transition-shadow">
          <div className="flex justify-between items-start mb-2">
            <h4 className="font-medium text-slate-800">{rel.contract_id}</h4>
            <span className="px-2 py-1 bg-orange-100 text-orange-800 text-xs rounded">{rel.role}</span>
          </div>
          <div className="flex items-center gap-2 mb-2">
            <Users className="w-4 h-4 text-slate-400" />
            <span className="font-medium text-sm">{rel.party_name}</span>
          </div>
          <p className="text-sm text-slate-600">{rel.context}</p>
        </div>
      ))}
    </div>
  );

  const renderAllLevelResults = (result: SearchResult) => (
    <div className="space-y-6">
      {result.documents && result.documents.length > 0 && renderDocumentResults(result.documents)}
      {result.sections && result.sections.length > 0 && renderSectionResults(result.sections)}
      {result.clauses && result.clauses.length > 0 && renderClauseResults(result.clauses)}
      {result.relationships && result.relationships.length > 0 && renderRelationshipResults(result.relationships)}
    </div>
  );

  return (
    <div className={`space-y-6 ${className}`}>
      <div className="flex items-center justify-between">
        <h2 className="text-xl font-semibold text-slate-800">Search Results</h2>
        {totalCount && (
          <span className="text-sm text-slate-500">
            {totalCount} total results
          </span>
        )}
      </div>

      {processedResults.map((result, idx) => (
        <div key={idx} className="bg-white rounded-lg border border-slate-200 p-6">
          {searchLevel === 'document' && result.documents && renderDocumentResults(result.documents)}
          {searchLevel === 'section' && result.sections && renderSectionResults(result.sections)}
          {searchLevel === 'clause' && result.clauses && renderClauseResults(result.clauses)}
          {searchLevel === 'relationship' && result.relationships && renderRelationshipResults(result.relationships)}
          {searchLevel === 'all' && renderAllLevelResults(result)}
        </div>
      ))}
    </div>
  );
};