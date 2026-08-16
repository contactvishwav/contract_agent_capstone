import React, { useEffect, useState } from 'react';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/shared/ui/card';
import { AlertCircle, Gauge, Target } from 'lucide-react';
import { useAuth } from '../contexts/AuthContext';
import { getLatestEvaluation, EvaluationResults } from '../services/adminEvaluationsApi';

/**
 * Phase 5 (MLOps governance harness) admin dashboard: renders the latest
 * Recall@K/nDCG@K results backend/scripts/evaluate_retrieval.py wrote for
 * the golden dataset (backend/tests/evals/golden_dataset.json). Read-only -
 * the real authorization is requires_role(ADMIN) on GET /api/admin/
 * evaluations itself; this ADMIN check is the same UX-only gating pattern
 * as AccountPage.tsx's InviteSection/PendingReviewsSection.
 */
export const AdminEvaluationsPage: React.FC = () => {
  const { session } = useAuth();
  const [results, setResults] = useState<EvaluationResults | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);

  useEffect(() => {
    if (session?.role !== 'ADMIN') return;
    setLoading(true);
    getLatestEvaluation()
      .then(setResults)
      .catch((err) => setError(err instanceof Error ? err.message : 'Could not load evaluation metrics'))
      .finally(() => setLoading(false));
  }, [session?.role]);

  if (session?.role !== 'ADMIN') return null;

  return (
    <div className="space-y-6" data-testid="admin-evaluations-page">
      <div>
        <h2 className="text-xl font-semibold text-slate-800">Retrieval evaluation</h2>
        <p className="text-sm text-slate-500">
          Offline Recall@K and nDCG@K for the golden query dataset, computed by{' '}
          <code className="rounded bg-slate-100 px-1 py-0.5 text-xs">backend/scripts/evaluate_retrieval.py</code>{' '}
          against real document-level retrieval.
        </p>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
          <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      {loading && !results && <p className="text-sm text-slate-500">Loading evaluation metrics…</p>}

      {results && !results.available && (
        <div className="rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-sm text-amber-800">
          {results.message || 'No evaluation has been run yet.'}
        </div>
      )}

      {results?.available && results.aggregate && (
        <>
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
            <Card className="border-slate-200">
              <CardHeader>
                <div className="flex items-center gap-2 text-blue-600 mb-1">
                  <Target className="h-5 w-5" />
                  <span className="text-xs font-semibold uppercase tracking-wide">Retrieval Coverage</span>
                </div>
                <CardTitle className="text-3xl" data-testid="eval-recall-metric">
                  {(results.aggregate.mean_recall_at_k * 100).toFixed(1)}%
                </CardTitle>
                <CardDescription>Recall@{results.k}</CardDescription>
              </CardHeader>
            </Card>
            <Card className="border-slate-200">
              <CardHeader>
                <div className="flex items-center gap-2 text-purple-600 mb-1">
                  <Gauge className="h-5 w-5" />
                  <span className="text-xs font-semibold uppercase tracking-wide">Ranking Quality</span>
                </div>
                <CardTitle className="text-3xl" data-testid="eval-ndcg-metric">
                  {(results.aggregate.mean_ndcg_at_k * 100).toFixed(1)}%
                </CardTitle>
                <CardDescription>nDCG@{results.k}</CardDescription>
              </CardHeader>
            </Card>
          </div>

          <Card className="border-slate-200">
            <CardHeader>
              <CardTitle className="text-lg">Per-query results</CardTitle>
              <CardDescription>
                {results.query_count} golden queries · generated{' '}
                {results.generated_at ? new Date(results.generated_at).toLocaleString() : 'unknown'}
              </CardDescription>
            </CardHeader>
            <CardContent>
              <div className="overflow-x-auto">
                <table className="w-full text-sm" data-testid="eval-per-query-table">
                  <thead>
                    <tr className="border-b border-slate-200 text-left text-slate-500">
                      <th className="py-2 pr-4">Query</th>
                      <th className="py-2 pr-4">Expected</th>
                      <th className="py-2 pr-4">Retrieved</th>
                      <th className="py-2 pr-4">Recall</th>
                      <th className="py-2 pr-4">nDCG</th>
                    </tr>
                  </thead>
                  <tbody>
                    {(results.per_query || []).map((row) => (
                      <tr key={row.id} className="border-b border-slate-100 align-top">
                        <td className="py-2 pr-4 max-w-xs truncate" title={row.query}>{row.query}</td>
                        <td className="py-2 pr-4 text-slate-500">{row.expected_filenames.join(', ')}</td>
                        <td className="py-2 pr-4 text-slate-500">{row.retrieved_filenames.join(', ') || '—'}</td>
                        <td className="py-2 pr-4">{(row.recall_at_k * 100).toFixed(0)}%</td>
                        <td className="py-2 pr-4">{(row.ndcg_at_k * 100).toFixed(0)}%</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </CardContent>
          </Card>
        </>
      )}
    </div>
  );
};
