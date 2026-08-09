import { apiFetch } from '../lib/apiClient';

export type ModelWorkflow = 'chat' | 'analysis' | 'upload';

export interface ModelOption {
  id: string;
  provider: string;
  display_label: string;
  configured: boolean;
  capabilities: string[];
  production_allowed: boolean;
  fallback_eligible: boolean;
  cost_class: string;
  latency_class: string;
  deprecated: boolean;
}

export interface ModelRegistryResponse {
  workflow: ModelWorkflow;
  models: ModelOption[];
  default_model: string | null;
  embedding: {
    provider: string;
    model: string;
    dimensions: number;
    user_selectable: false;
    reason: string;
  };
  fallback_policy: {
    automatic_cross_provider: false;
    disclosure_required: true;
    legal_analysis: string;
  };
}

export async function getWorkflowModels(workflow: ModelWorkflow): Promise<ModelRegistryResponse> {
  const response = await apiFetch(`/api/models?workflow=${encodeURIComponent(workflow)}`);
  if (!response.ok) throw new Error('Available models could not be loaded');
  return response.json();
}
