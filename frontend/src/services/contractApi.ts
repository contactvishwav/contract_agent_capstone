import { apiFetch } from '../lib/apiClient';

export interface ContractSummary {
  contract_id: string;
  filename: string;
  upload_date: string | null;
  model_used: string;
  analysis_completed: boolean;
  risk_score?: number | null;
  risk_level?: string | null;
}

export interface PersistedAnalysisResponse {
  state: 'not_analyzed' | 'processing' | 'completed' | 'completed_with_errors' | string;
  source: 'contract' | 'task_state' | 'persisted_analysis' | 'legacy_contract_summary';
  legacy_summary: boolean;
  filename: string;
  analysis_id?: string | null;
  created_at?: string | null;
  task_id?: string | null;
  task_state?: string | null;
  status_url?: string | null;
  analysis?: Record<string, unknown> | null;
}

export async function listContracts(): Promise<ContractSummary[]> {
  const response = await apiFetch('/api/documents');
  if (!response.ok) {
    throw new Error(`Failed to list contracts: ${response.statusText}`);
  }
  return response.json();
}

export async function getLatestContractAnalysis(contractId: string): Promise<PersistedAnalysisResponse> {
  const response = await apiFetch(`/api/intelligence/contracts/${encodeURIComponent(contractId)}/analysis`);
  if (!response.ok) {
    throw new Error(`Failed to load contract analysis: ${response.statusText}`);
  }
  return response.json();
}
