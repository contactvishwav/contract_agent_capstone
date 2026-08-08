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

export async function listContracts(): Promise<ContractSummary[]> {
  const response = await apiFetch('/api/documents');
  if (!response.ok) {
    throw new Error(`Failed to list contracts: ${response.statusText}`);
  }
  return response.json();
}
