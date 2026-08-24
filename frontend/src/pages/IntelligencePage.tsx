import React, { useCallback, useEffect, useState } from 'react';
import { DocumentUpload } from '../components/features/contracts/DocumentUpload';
import { ContractIntelligence } from '../components/features/intelligence/ContractIntelligence';
import { AgentWorkflowTracker } from '../components/features/agents/AgentWorkflowTracker';
import type { WorkflowStatus } from '../components/features/agents/AgentWorkflowTracker';
import { Card } from '../components/shared/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/shared/ui/select';
import { useContractHistory } from '../contexts/ContractHistoryContext';
import { useAuth } from '../contexts/AuthContext';
import { archiveContract } from '../services/contractApi';
import { Archive as ArchiveIcon } from 'lucide-react';
import { getWorkflowModels, ModelOption } from '../services/modelRegistryApi';

interface UploadResult {
  filename: string;
  status: string;
  contract_id?: string;
  existing_contract_id?: string;
  details: string;
  model_used: string;
}

export const IntelligencePage: React.FC = () => {
  const [selectedModel, setSelectedModel] = useState('');
  const [models, setModels] = useState<ModelOption[]>([]);
  const [modelError, setModelError] = useState<string | null>(null);
  // Real bug found live in production: DocumentUpload had no way to know
  // the registry fetch below was still in flight, so an upload started
  // before it resolved sent model= (empty) and got a real 400 from the
  // backend. modelsLoading is the missing "still in flight" signal -
  // starts true, flips false in both the success and failure branch of
  // the effect below, so DocumentUpload can tell "not ready yet" apart
  // from "ready with nothing usable" (modelError) and "ready" (selectedModel).
  const [modelsLoading, setModelsLoading] = useState(true);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<WorkflowStatus | null>(null);
  const [isUploading, setIsUploading] = useState(false);
  const [archiveTarget, setArchiveTarget] = useState<{ contract_id: string; filename: string } | null>(null);
  const [archiveError, setArchiveError] = useState<string | null>(null);
  const [isArchiving, setIsArchiving] = useState(false);
  const { session } = useAuth();
  const {
    contracts,
    addContract,
    updateContract,
    getContract,
    removeContract,
    selectedContractId,
    setSelectedContract,
  } = useContractHistory();
  const selectedContract = selectedContractId ? getContract(selectedContractId) : undefined;

  useEffect(() => {
    let active = true;
    getWorkflowModels('analysis').then((registry) => {
      if (!active) return;
      setModels(registry.models);
      setSelectedModel(registry.default_model || registry.models[0]?.id || '');
      setModelError(registry.models.length ? null : 'No compatible analysis model is configured.');
      setModelsLoading(false);
    }).catch(() => {
      if (!active) return;
      setModelError('Available analysis models could not be loaded.');
      setModelsLoading(false);
    });
    return () => { active = false; };
  }, []);

  const handleUploadComplete = (result: UploadResult) => {
    setUploadResult(result);
    setIsUploading(false);

    if (!result.contract_id) return;

    // A duplicate upload surfaces the pre-existing contract_id (see
    // document_upload.py's duplicate branch) so the analysis panel below
    // doesn't dead-end - but addContract() unconditionally overwrites
    // analysis_completed/risk_score/analysis_results for that contract_id
    // (ContractHistoryContext.tsx's addContract fully replaces any
    // existing record with the same id). If this contract was already
    // analyzed earlier in this session, blindly calling addContract here
    // would silently wipe that real result back to "not analyzed yet" -
    // a real regression this fix must not introduce. Only add a fresh
    // blank record when the contract isn't already known locally;
    // otherwise leave its existing history entry (and analysis state)
    // untouched and just select it.
    if (result.status === 'duplicate' && getContract(result.contract_id)) {
      setSelectedContract(result.contract_id);
      return;
    }

    addContract({
      contract_id: result.contract_id,
      filename: result.filename,
      upload_date: new Date().toISOString(),
      model_used: result.model_used,
      analysis_completed: false
    });
  };

  const handleUploadStart = () => {
    setIsUploading(true);
  };

  const handleWorkflowUpdate = useCallback((status: WorkflowStatus) => {
    setWorkflowStatus(status);
  }, []);

  const handleAnalysisComplete = useCallback((contractId: string, riskScore?: number, riskLevel?: string, results?: unknown) => {
    updateContract(contractId, {
      analysis_completed: true,
      risk_score: riskScore,
      risk_level: riskLevel,
      analysis_results: results
    });
  }, [updateContract]);

  const handleArchive = async () => {
    if (!archiveTarget) return;
    setIsArchiving(true);
    setArchiveError(null);
    try {
      await archiveContract(archiveTarget.contract_id);
      removeContract(archiveTarget.contract_id);
      if (uploadResult?.contract_id === archiveTarget.contract_id) setUploadResult(null);
      setArchiveTarget(null);
    } catch (error) {
      setArchiveError(error instanceof Error ? error.message : 'Could not archive contract');
    } finally {
      setIsArchiving(false);
    }
  };

  return (
    <div className="space-y-8">
      {/* Header Section */}
      <div className="text-center bg-white rounded-lg p-8 shadow-sm border border-slate-200">
        <h1 className="text-3xl font-bold text-slate-800 mb-3">Contract Intelligence Platform</h1>
        <p className="text-lg text-slate-600 max-w-2xl mx-auto">
          Upload legal contracts and leverage AI-powered analysis for comprehensive insights, 
          risk assessment, and compliance review.
        </p>
      </div>

      {/* Model Selection */}
      <div className="flex justify-center">
        <div className="bg-white rounded-lg p-4 shadow-sm border border-slate-200">
          <div className="flex items-center gap-3">
            <label className="text-sm font-semibold text-slate-700">AI Model:</label>
            <Select value={selectedModel} onValueChange={setSelectedModel} disabled={!models.length}>
              <SelectTrigger className="w-56 border-slate-300">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {models.map((model) => (
                  <SelectItem key={model.id} value={model.id}>{model.display_label}</SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          {modelError && <p className="mt-2 text-sm text-red-700" role="alert">{modelError}</p>}
        </div>
      </div>



      {/* Contract History */}
      {contracts.length > 0 && (
        <div className="bg-white rounded-lg p-6 shadow-sm border border-slate-200 mb-8">
          <h2 className="text-xl font-semibold text-slate-800 mb-4">Recent Contracts</h2>
          <div className="space-y-2">
            {contracts.slice(0, 5).map((contract) => (
              <div
                key={contract.contract_id} 
                className={`flex w-full items-center gap-2 rounded-lg border p-1 transition-colors ${
                  contract.contract_id === selectedContractId
                    ? 'border-blue-400 bg-blue-50'
                    : 'border-transparent bg-slate-50 hover:bg-slate-100'
                }`}
              >
                <button
                  type="button"
                  aria-pressed={contract.contract_id === selectedContractId}
                  className="flex min-w-0 flex-1 items-center justify-between rounded-md p-2 text-left focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500"
                  onClick={() => setSelectedContract(contract.contract_id)}
                >
                  <div className="min-w-0">
                    <p className="truncate font-medium text-slate-800">{contract.filename}</p>
                    <p className="text-sm text-slate-500">
                      {new Date(contract.upload_date).toLocaleDateString()} • {contract.model_used}
                      {contract.analysis_completed && contract.risk_level && (
                        <span className={`ml-2 rounded px-2 py-1 text-xs ${
                          contract.risk_level === 'HIGH' || contract.risk_level === 'CRITICAL'
                            ? 'bg-red-100 text-red-700'
                            : contract.risk_level === 'MEDIUM'
                            ? 'bg-yellow-100 text-yellow-700'
                            : 'bg-green-100 text-green-700'
                        }`}>
                          {contract.risk_level} Risk
                        </span>
                      )}
                    </p>
                  </div>
                  <span className="ml-3 text-sm text-slate-500">
                    {contract.contract_id === selectedContractId ? 'Selected' : 'Open analysis'}
                  </span>
                </button>
                {session?.role === 'ADMIN' && (
                  <button
                    type="button"
                    className="rounded-md p-2 text-slate-500 hover:bg-red-50 hover:text-red-700 focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-red-500"
                    aria-label={`Archive ${contract.filename}`}
                    onClick={() => { setArchiveError(null); setArchiveTarget(contract); }}
                  >
                    <ArchiveIcon className="h-4 w-4" />
                  </button>
                )}
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Main Content Grid */}
      <div className="grid grid-cols-1 xl:grid-cols-2 gap-8">
        {/* Upload Section */}
        <Card className="bg-white border-slate-200 shadow-sm">
          <div className="p-6">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-2 h-2 bg-blue-500 rounded-full"></div>
              <h2 className="text-xl font-semibold text-slate-800">Document Upload</h2>
            </div>
            <p className="text-slate-600 text-sm mb-6">
              Upload PDF contracts for AI-powered analysis and extraction of key legal terms.
            </p>
            <DocumentUpload
              onUploadComplete={handleUploadComplete}
              modelSelection={selectedModel}
              modelsLoading={modelsLoading}
              modelError={modelError}
              onWorkflowUpdate={handleWorkflowUpdate}
              onUploadStart={handleUploadStart}
            />
            
            {/* PDF Processing Workflow */}
            {((workflowStatus?.agent_executions.length ?? 0) > 0 || isUploading || uploadResult) && (
              <div className="mt-4">
                <div className="bg-blue-50 border border-blue-200 rounded-lg p-4">
                  <h4 className="text-sm font-semibold text-blue-800 mb-2">🤖 PDF Processing Agent</h4>
                  <div className="text-xs text-blue-600">
                    {isUploading ? '⏳ Processing PDF...' : '✅ PDF processed successfully'}
                  </div>
                </div>
              </div>
            )}
          </div>
        </Card>

        {/* Analysis Section */}
        <Card className="bg-white border-slate-200 shadow-sm">
          <div className="p-6">
            <div className="flex items-center gap-2 mb-4">
              <div className="w-2 h-2 bg-green-500 rounded-full"></div>
              <h2 className="text-xl font-semibold text-slate-800">Intelligence Analysis</h2>
            </div>
            <p className="text-slate-600 text-sm mb-6">
              Comprehensive AI analysis including risk assessment, clause extraction, and compliance review.
            </p>
            {selectedContract ? (
              <>
                {/* Intelligence Analysis Workflow */}
                {workflowStatus && workflowStatus.agent_executions.length > 0 && (
                  <div className="mb-4">
                    <AgentWorkflowTracker
                      workflowStatus={{
                        ...workflowStatus,
                        agent_executions: workflowStatus.agent_executions.filter((execution) => execution.agent_name !== 'PDF Processing Agent')
                      }}
                    />
                  </div>
                )}
                <ContractIntelligence 
                  key={selectedContract.contract_id}
                  contractId={selectedContract.contract_id}
                  filename={selectedContract.filename}
                  model={selectedModel}
                  onWorkflowUpdate={handleWorkflowUpdate}
                  onAnalysisComplete={handleAnalysisComplete}
                />
              </>
            ) : (
              <div className="text-center py-12 border-2 border-dashed border-slate-300 rounded-lg">
                <div className="text-slate-400 text-4xl mb-3">📄</div>
                <p className="text-slate-500 font-medium">Upload a contract to begin analysis</p>
                <p className="text-slate-400 text-sm mt-1">
                  AI will extract clauses, assess risks, and provide recommendations
                </p>
              </div>
            )}
          </div>
        </Card>
      </div>

      {archiveTarget && (
        <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/40 p-4" role="presentation">
          <div
            role="dialog"
            aria-modal="true"
            aria-labelledby="archive-contract-title"
            className="w-full max-w-md rounded-lg bg-white p-6 shadow-xl"
          >
            <h2 id="archive-contract-title" className="text-lg font-semibold text-slate-900">Archive {archiveTarget.filename}?</h2>
            <p className="mt-3 text-sm text-slate-600">
              This removes the contract from Recent Contracts, Chat, search, and new analysis. Its audit evidence and existing conversation records are retained but hidden. This is not permanent deletion.
            </p>
            {archiveError && <p className="mt-3 text-sm text-red-700" role="alert">{archiveError}</p>}
            <div className="mt-6 flex justify-end gap-2">
              <button
                type="button"
                className="rounded-md border px-4 py-2 text-sm"
                disabled={isArchiving}
                onClick={() => setArchiveTarget(null)}
              >
                Cancel
              </button>
              <button
                type="button"
                className="rounded-md bg-red-700 px-4 py-2 text-sm text-white disabled:opacity-50"
                disabled={isArchiving}
                onClick={handleArchive}
              >
                {isArchiving ? 'Archiving…' : 'Archive contract'}
              </button>
            </div>
          </div>
        </div>
      )}
    </div>
  );
};
