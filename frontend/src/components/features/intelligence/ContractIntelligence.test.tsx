import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ContractIntelligence } from './ContractIntelligence';

const mocks = vi.hoisted(() => ({
  getLatest: vi.fn(),
  apiFetch: vi.fn(),
}));

vi.mock('../../../services/contractApi', () => ({
  getLatestContractAnalysis: mocks.getLatest,
}));
vi.mock('../../../lib/apiClient', () => ({ apiFetch: mocks.apiFetch }));

const persisted = {
  contract_id: 'C1',
  analysis_complete: true,
  model_used: 'gemini-2.5-flash',
  execution_path: 'langgraph_traditional_explicit',
  results: {
    clauses: [],
    violations: [],
    redlines: [],
    risk_assessment: {
      overall_risk_score: 42,
      risk_level: 'MEDIUM',
      critical_issues: [],
      recommendations: [],
    },
  },
};

function deferred<T>() {
  let resolve!: (value: T) => void;
  const promise = new Promise<T>((done) => { resolve = done; });
  return { promise, resolve };
}

describe('ContractIntelligence persisted restoration', () => {
  beforeEach(() => vi.clearAllMocks());

  it('loads a completed analysis without posting a new model request', async () => {
    mocks.getLatest.mockResolvedValue({
      state: 'completed', source: 'persisted_analysis', legacy_summary: false,
      filename: 'Clean_MSA.pdf', analysis: persisted,
    });
    render(<ContractIntelligence contractId="C1" filename="Clean_MSA.pdf" />);

    expect(await screen.findByText('42/100')).toBeInTheDocument();
    expect(screen.getByText('Clean_MSA.pdf')).toBeInTheDocument();
    expect(screen.getByText('langgraph_traditional_explicit')).toBeInTheDocument();
    expect(mocks.apiFetch).not.toHaveBeenCalled();
  });

  it('shows not analyzed and ignores a late response from the prior contract', async () => {
    const first = deferred<any>();
    mocks.getLatest
      .mockReturnValueOnce(first.promise)
      .mockResolvedValueOnce({
        state: 'not_analyzed', source: 'contract', legacy_summary: false,
        filename: 'Clean_SOW.pdf', analysis: null,
      });
    const view = render(<ContractIntelligence contractId="C1" filename="Clean_MSA.pdf" />);
    view.rerender(<ContractIntelligence contractId="C2" filename="Clean_SOW.pdf" />);

    expect(await screen.findByText('Not analyzed yet')).toBeInTheDocument();
    await act(async () => {
      first.resolve({
        state: 'completed', source: 'persisted_analysis', legacy_summary: false,
        filename: 'Clean_MSA.pdf', analysis: persisted,
      });
      await first.promise;
    });

    await waitFor(() => expect(screen.getByText('Clean_SOW.pdf')).toBeInTheDocument());
    expect(screen.queryByText('42/100')).not.toBeInTheDocument();
  });
});
