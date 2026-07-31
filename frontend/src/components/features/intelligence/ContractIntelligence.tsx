import React, { useState } from 'react';
import { Button } from '../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle } from '../../shared/ui/card';
import { Badge } from '../../shared/ui/badge';
import { Clock, Brain, XCircle, FileText, AlertTriangle, Shield, Wifi, RefreshCw } from 'lucide-react';
import { DetailModal } from './DetailModal';
import { ClausesDetail } from './ClausesDetail';
import { ViolationsDetail } from './ViolationsDetail';
import { RiskDetail } from './RiskDetail';
import { useModal } from '../../../lib/useModal';

interface ContractClause {
  clause_type: string;
  content: string;
  risk_level: string;
  confidence_score: number;
  location: string;
}

interface PolicyViolation {
  clause_type: string;
  issue: string;
  severity: string;
  suggested_fix: string;
  clause_content: string;
}

interface RiskAssessment {
  overall_risk_score: number;
  risk_level: string;
  critical_issues: string[];
  recommendations: string[];
}

interface IntelligenceResults {
  clauses: ContractClause[];
  violations: PolicyViolation[];
  risk_assessment: RiskAssessment;
  redlines: any[];
}

interface ContractIntelligenceProps {
  contractId: string;
  model?: string;
  onWorkflowUpdate?: (status: any) => void;
  onAnalysisComplete?: (contractId: string, riskScore?: number, riskLevel?: string) => void;
}

const TASK_POLL_INTERVAL_MS = 1500;
const TASK_POLL_TIMEOUT_MS = 5 * 60 * 1000;

async function pollTaskStatus(statusUrl: string): Promise<any> {
  const deadline = Date.now() + TASK_POLL_TIMEOUT_MS;

  while (Date.now() < deadline) {
    const response = await fetch(statusUrl);
    if (!response.ok) {
      throw new Error(`Failed to check analysis status: ${response.statusText}`);
    }
    const body = await response.json();

    if (body.status === 'SUCCESS') {
      return body.result;
    }
    if (body.status === 'FAILURE') {
      throw new Error(body.error || 'Analysis task failed');
    }
    // PENDING / STARTED - keep polling.
    await new Promise((resolve) => setTimeout(resolve, TASK_POLL_INTERVAL_MS));
  }

  throw new Error('Analysis is taking longer than expected. Please check back shortly.');
}

export const ContractIntelligence: React.FC<ContractIntelligenceProps> = ({
  contractId, 
  model = 'gemini-2.5-flash',
  onWorkflowUpdate,
  onAnalysisComplete
}) => {
  const [results, setResults] = useState<IntelligenceResults | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [networkError, setNetworkError] = useState(false);
  const { openModal, closeModal, isOpen } = useModal();

  const analyzeContract = async () => {
    setLoading(true);
    setError(null);
    setNetworkError(false);
    
    // Start polling for workflow status
    const pollWorkflow = setInterval(async () => {
      try {
        const workflowResponse = await fetch('/api/workflow/status');
        if (workflowResponse.ok) {
          const workflowData = await workflowResponse.json();
          onWorkflowUpdate?.(workflowData);
        }
      } catch (e) {
        // Ignore workflow polling errors
      }
    }, 500);
    
    try {
      const response = await fetch(`/api/intelligence/contracts/${contractId}/analyze?model=${model}`, {
        method: 'POST',
      });

      if (!response.ok) {
        if (response.status === 404) {
          throw new Error('Contract not found. Please verify the contract ID.');
        }
        if (response.status >= 500) {
          throw new Error('Server error. Please try again later.');
        }
        throw new Error(`Analysis failed: ${response.statusText}`);
      }

      // Analysis now runs as a Celery task - the POST above just enqueues
      // it and returns a task_id immediately. Poll the real task status
      // until it reaches a terminal state (SUCCESS/FAILURE) instead of
      // expecting the results inline in this response.
      const { status_url } = await response.json();
      const data = await pollTaskStatus(status_url);

      if (!data.results) {
        throw new Error('No analysis results returned. The contract may be invalid or corrupted.');
      }

      setResults(data.results);

      // Report analysis completion with full results
      if (data.results?.risk_assessment) {
        onAnalysisComplete?.(contractId, data.results.risk_assessment.overall_risk_score, data.results.risk_assessment.risk_level, data.results);
      }
      
      // Final workflow status update
      setTimeout(async () => {
        try {
          const workflowResponse = await fetch('/api/workflow/status');
          if (workflowResponse.ok) {
            const workflowData = await workflowResponse.json();
            onWorkflowUpdate?.(workflowData);
          }
        } catch (e) {
          // Ignore final workflow polling error
        }
      }, 1000);
    } catch (err) {
      if (err instanceof TypeError && err.message.includes('fetch')) {
        setNetworkError(true);
        setError('Network connection failed. Please check your internet connection.');
      } else {
        setError(err instanceof Error ? err.message : 'Analysis failed');
      }
    } finally {
      clearInterval(pollWorkflow);
      setLoading(false);
    }
  };

  const getRiskColor = (level: string) => {
    switch (level.toUpperCase()) {
      case 'CRITICAL': return 'bg-red-500 text-white';
      case 'HIGH': return 'bg-orange-500 text-white';
      case 'MEDIUM': return 'bg-yellow-500 text-white';
      case 'LOW': return 'bg-green-500 text-white';
      default: return 'bg-gray-500 text-white';
    }
  };

  const getViolationSeverityColor = (violations: PolicyViolation[]) => {
    if (!violations || violations.length === 0) return 'text-slate-600';
    
    const hasCritical = violations.some(v => v.severity.toUpperCase() === 'CRITICAL');
    const hasHigh = violations.some(v => v.severity.toUpperCase() === 'HIGH');
    
    if (hasCritical) return 'text-red-600';
    if (hasHigh) return 'text-orange-600';
    return 'text-slate-600';
  };

  const renderEmptyResults = () => (
    <Card className="border-slate-200 bg-slate-50">
      <CardContent className="pt-6 text-center py-12">
        <FileText className="h-12 w-12 text-slate-400 mx-auto mb-4" />
        <h3 className="text-lg font-medium text-slate-600 mb-2">No Analysis Results</h3>
        <p className="text-sm text-slate-500 mb-4">
          The contract analysis returned no results. This may indicate the document is not a valid contract.
        </p>
        <Button variant="outline" onClick={analyzeContract}>
          <RefreshCw className="h-4 w-4 mr-2" />
          Retry Analysis
        </Button>
      </CardContent>
    </Card>
  );

  const renderNetworkError = () => (
    <Card className="border-red-200 bg-red-50">
      <CardContent className="pt-6 text-center py-12">
        <Wifi className="h-12 w-12 text-red-400 mx-auto mb-4" />
        <h3 className="text-lg font-medium text-red-600 mb-2">Connection Failed</h3>
        <p className="text-sm text-red-700 mb-4">
          Unable to connect to the analysis service. Please check your connection and try again.
        </p>
        <Button variant="outline" onClick={analyzeContract} className="border-red-300">
          <RefreshCw className="h-4 w-4 mr-2" />
          Retry Connection
        </Button>
      </CardContent>
    </Card>
  );

  const hasPartialResults = (results: IntelligenceResults) => {
    return results && (
      !results.clauses || results.clauses.length === 0 ||
      !results.violations || 
      !results.risk_assessment
    );
  };

  return (
    <div className="space-y-6">
      {/* Header */}
      <div className="flex items-center justify-between">
        <div>
          <h3 className="text-lg font-semibold text-slate-800">AI Analysis</h3>
          <p className="text-sm text-slate-600">Contract {contractId}</p>
        </div>
        <Button 
          onClick={analyzeContract} 
          disabled={loading}
          className="flex items-center gap-2"
        >
          <Brain className="h-4 w-4" />
          {loading ? 'Analyzing...' : 'Analyze'}
        </Button>
      </div>

      {/* Network Error State */}
      {networkError && renderNetworkError()}

      {/* Error Display */}
      {error && !networkError && (
        <Card className="border-red-200 bg-red-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-red-700 mb-3">
              <XCircle className="h-4 w-4" />
              <span className="font-medium">Analysis Error</span>
            </div>
            <p className="text-sm text-red-600 mb-3">{error}</p>
            <Button variant="outline" size="sm" onClick={analyzeContract}>
              Try Again
            </Button>
          </CardContent>
        </Card>
      )}

      {/* Loading State */}
      {loading && (
        <Card className="border-slate-200">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-blue-600">
              <Clock className="h-4 w-4 animate-spin" />
              <span>Multi-agent analysis in progress...</span>
            </div>
          </CardContent>
        </Card>
      )}

      {/* Empty Results Fallback */}
      {!loading && !error && results && 
       (!results.clauses || results.clauses.length === 0) && 
       (!results.violations || results.violations.length === 0) && 
       !results.risk_assessment && renderEmptyResults()}

      {/* Partial Results Warning */}
      {results && hasPartialResults(results) && (
        <Card className="border-yellow-200 bg-yellow-50">
          <CardContent className="pt-6">
            <div className="flex items-center gap-2 text-yellow-700">
              <AlertTriangle className="h-4 w-4" />
              <span className="font-medium">Partial Analysis Results</span>
            </div>
            <p className="text-sm text-yellow-600 mt-1">
              Some analysis components may have failed. Results shown are incomplete.
            </p>
          </CardContent>
        </Card>
      )}

      {/* Results */}
      {results && results.risk_assessment && (
        <div className="space-y-4">
          {/* Overview Cards - Clickable */}
          <div className="grid grid-cols-3 gap-4">
            <Card 
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-blue-300 transition-all duration-200"
              onClick={() => openModal('risk')}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <Shield className="h-4 w-4" />
                  Risk Score
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-slate-800">
                  {results.risk_assessment?.overall_risk_score || 0}/100
                </div>
                <Badge className={getRiskColor(results.risk_assessment?.risk_level || 'UNKNOWN')}>
                  {results.risk_assessment?.risk_level || 'UNKNOWN'}
                </Badge>
                <p className="text-xs text-blue-600 mt-2 font-medium">Click for details →</p>
              </CardContent>
            </Card>

            <Card 
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-orange-300 transition-all duration-200"
              onClick={() => openModal('violations')}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <AlertTriangle className="h-4 w-4" />
                  Violations
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className={`text-2xl font-bold ${getViolationSeverityColor(results.violations || [])}`}>
                  {results.violations?.length || 0}
                </div>
                <p className="text-xs text-slate-500 mt-1">Policy violations found</p>
                <p className="text-xs text-orange-600 mt-1 font-medium">Click for details →</p>
              </CardContent>
            </Card>

            <Card 
              className="border-slate-200 cursor-pointer hover:shadow-md hover:border-green-300 transition-all duration-200"
              onClick={() => openModal('clauses')}
            >
              <CardHeader className="pb-2">
                <CardTitle className="text-sm text-slate-600 flex items-center gap-2">
                  <FileText className="h-4 w-4" />
                  Clauses
                </CardTitle>
              </CardHeader>
              <CardContent>
                <div className="text-2xl font-bold text-slate-800">
                  {results.clauses?.length || 0}
                </div>
                <p className="text-xs text-slate-500 mt-1">Key clauses extracted</p>
                <p className="text-xs text-green-600 mt-1 font-medium">Click for details →</p>
              </CardContent>
            </Card>
          </div>

          {/* Critical Issues Preview */}
          {results.risk_assessment?.critical_issues?.length > 0 && (
            <Card className="border-red-200 bg-red-50">
              <CardHeader>
                <CardTitle className="text-red-700 text-sm flex items-center gap-2">
                  <XCircle className="h-4 w-4" />
                  Critical Issues Detected
                </CardTitle>
              </CardHeader>
              <CardContent>
                <p className="text-sm text-red-800 mb-3">
                  {results.risk_assessment.critical_issues.length} critical issues require immediate attention
                </p>
                <Button 
                  variant="outline" 
                  size="sm" 
                  className="border-red-300 text-red-700 hover:bg-red-100"
                  onClick={() => openModal('risk')}
                >
                  Review Critical Issues
                </Button>
              </CardContent>
            </Card>
          )}
        </div>
      )}

      {/* Detail Modals with Contract ID */}
      <DetailModal
        isOpen={isOpen('clauses')}
        onClose={closeModal}
        title={`Contract Clauses Analysis (${results?.clauses?.length || 0} found)`}
      >
        {results?.clauses && results.clauses.length > 0 ? (
          <ClausesDetail clauses={results.clauses} contractId={contractId} />
        ) : (
          <div className="text-center py-8">
            <FileText className="h-12 w-12 text-slate-400 mx-auto mb-4" />
            <p className="text-slate-600">No clauses were extracted from this contract.</p>
          </div>
        )}
      </DetailModal>

      <DetailModal
        isOpen={isOpen('violations')}
        onClose={closeModal}
        title={`Policy Violations Review (${results?.violations?.length || 0} found)`}
      >
        {results?.violations && results.violations.length > 0 ? (
          <ViolationsDetail violations={results.violations} contractId={contractId} />
        ) : (
          <div className="text-center py-8">
            <AlertTriangle className="h-12 w-12 text-green-400 mx-auto mb-4" />
            <p className="text-slate-600">No policy violations detected in this contract.</p>
          </div>
        )}
      </DetailModal>

      <DetailModal
        isOpen={isOpen('risk')}
        onClose={closeModal}
        title="Comprehensive Risk Assessment"
      >
        {results?.risk_assessment ? (
          <RiskDetail riskAssessment={results.risk_assessment} contractId={contractId} />
        ) : (
          <div className="text-center py-8">
            <Shield className="h-12 w-12 text-slate-400 mx-auto mb-4" />
            <p className="text-slate-600">Risk assessment data is not available.</p>
          </div>
        )}
      </DetailModal>
    </div>
  );
};