import { EnhancedSearchParams } from '../components/features/search/EnhancedSearchInterface';
import { apiFetch } from '../lib/apiClient';

// Real bug found live against the production deployment: this was hardcoded
// to 'http://localhost:8000', so every call here tried to reach the
// browser's own machine instead of the deployed backend - every other API
// call in this app (DocumentUpload.tsx, ChatInput.tsx, apiClient.ts) uses a
// relative path against the current origin instead, which is what nginx's
// /api/ proxy (nginx.conf.template) and the Vite dev proxy (vite.config.ts)
// both expect.
const API_BASE_URL = '';

export interface EnhancedSearchResponse {
  success: boolean;
  search_level: string;
  results: any[];
}

export interface SectionType {
  value: string;
  label: string;
  description: string;
}

class EnhancedSearchApi {
  
  async searchContracts(params: EnhancedSearchParams): Promise<EnhancedSearchResponse> {
    const response = await apiFetch(`${API_BASE_URL}/api/contracts/search/enhanced`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
      },
      body: JSON.stringify({
        search_level: params.searchLevel,
        query: params.query || null,
        clause_types: params.clauseTypes.length > 0 ? params.clauseTypes : null,
        section_types: params.sectionTypes.length > 0 ? params.sectionTypes : null,
        parties: params.parties.length > 0 ? params.parties : null,
        contract_type: params.contractType || null,
        active: params.active
      })
    });

    if (!response.ok) {
      throw new Error(`Search failed: ${response.statusText}`);
    }

    return response.json();
  }

  async getClauseTypes(): Promise<string[]> {
    const response = await apiFetch(`${API_BASE_URL}/api/contracts/search/clause-types`);
    
    if (!response.ok) {
      throw new Error(`Failed to get clause types: ${response.statusText}`);
    }

    const data = await response.json();
    return data.clause_types;
  }

  async getSectionTypes(): Promise<SectionType[]> {
    const response = await apiFetch(`${API_BASE_URL}/api/contracts/search/section-types`);
    
    if (!response.ok) {
      throw new Error(`Failed to get section types: ${response.statusText}`);
    }

    const data = await response.json();
    return data.section_types;
  }

  async uploadEnhancedDocument(
    file: File, 
    model: string = 'gemini-2.5-flash',
    enableEmbeddings: boolean = true
  ): Promise<any> {
    const formData = new FormData();
    formData.append('file', file);

    const response = await apiFetch(
      `${API_BASE_URL}/api/documents/enhanced/upload?model=${model}&enable_embeddings=${enableEmbeddings}`,
      {
        method: 'POST',
        body: formData
      }
    );

    if (!response.ok) {
      throw new Error(`Enhanced upload failed: ${response.statusText}`);
    }

    return response.json();
  }

  async getEmbeddingStatus(contractId: string): Promise<any> {
    const response = await apiFetch(`${API_BASE_URL}/api/documents/enhanced/embedding-status/${contractId}`);
    
    if (!response.ok) {
      throw new Error(`Failed to get embedding status: ${response.statusText}`);
    }

    return response.json();
  }
}

export const enhancedSearchApi = new EnhancedSearchApi();