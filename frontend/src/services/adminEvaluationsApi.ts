import { apiFetch } from '../lib/apiClient';

export interface EvaluationQueryResult {
  id: string;
  query: string;
  expected_filenames: string[];
  retrieved_filenames: string[];
  recall_at_k: number;
  ndcg_at_k: number;
}

export interface EvaluationResults {
  available: boolean;
  message?: string;
  generated_at?: string;
  k?: number;
  search_level?: string;
  query_count?: number;
  aggregate?: {
    mean_recall_at_k: number;
    mean_ndcg_at_k: number;
  };
  per_query?: EvaluationQueryResult[];
}

export async function getLatestEvaluation(): Promise<EvaluationResults> {
  const response = await apiFetch('/api/admin/evaluations');
  if (!response.ok) throw new Error('Retrieval evaluation metrics could not be loaded');
  return response.json();
}
