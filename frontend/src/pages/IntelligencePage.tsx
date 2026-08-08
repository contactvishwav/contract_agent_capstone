import React, { useState } from 'react';
import { DocumentUpload } from '../components/features/contracts/DocumentUpload';
import { ContractIntelligence } from '../components/features/intelligence/ContractIntelligence';
import { AgentWorkflowTracker } from '../components/features/agents/AgentWorkflowTracker';
import { Card } from '../components/shared/ui/card';
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '../components/shared/ui/select';
import { useContractHistory } from '../contexts/ContractHistoryContext';

interface UploadResult {
  filename: string;
  status: string;
  contract_id?: string;
  existing_contract_id?: string;
  details: string;
  model_used: string;
}

export const IntelligencePage: React.FC = () => {
  const [selectedModel, setSelectedModel] = useState('gemini-2.5-flash');
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);
  const [workflowStatus, setWorkflowStatus] = useState<any>(null);
  const [isUploading, setIsUploading] = useState(false);
  const {
    contracts,
    addContract,
    updateContract,
    getContract,
    selectedContractId,
    setSelectedContract,
  } = useContractHistory();
  const selectedContract = selectedContractId ? getContract(selectedContractId) : undefined;

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

  const handleWorkflowUpdate = (status: any) => {
    setWorkflowStatus(status);
  };

  const handleAnalysisComplete = (contractId: string, riskScore?: number, riskLevel?: string, results?: any) => {
    updateContract(contractId, {
      analysis_completed: true,
      risk_score: riskScore,
      risk_level: riskLevel,
      analysis_results: results
    });
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
            <Select value={selectedModel} onValueChange={setSelectedModel}>
              <SelectTrigger className="w-56 border-slate-300">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {/* Real, confirmed bug found live: this said "Gemini 2.0
                Flash" while value="gemini-2.5-flash" - the label never
                matched what was actually selected. gemini-1.5-pro (the
                option below this comment previously) is now confirmed
                dead (404 NOT_FOUND from the real API, not just
                deprecated) - removed rather than relabeled, since there
                is no live model to correctly point it at. Note: this
                selector's value doesn't currently reach the real analysis
                LLM choice at all (a separate, larger, already-flagged bug
                in the upload/analyze wiring) - fixing the label/removing
                the dead option here doesn't change that. */}
                <SelectItem value="gemini-2.5-flash">Gemini 2.5 Flash</SelectItem>
                <SelectItem value="gpt-4o">GPT-4o</SelectItem>
                <SelectItem value="sonnet-3.5">Claude Sonnet 3.5</SelectItem>
              </SelectContent>
            </Select>
          </div>
        </div>
      </div>



      {/* Contract History */}
      {contracts.length > 0 && (
        <div className="bg-white rounded-lg p-6 shadow-sm border border-slate-200 mb-8">
          <h2 className="text-xl font-semibold text-slate-800 mb-4">Recent Contracts</h2>
          <div className="space-y-2">
            {contracts.slice(0, 5).map((contract) => (
              <button
                type="button"
                key={contract.contract_id} 
                aria-pressed={contract.contract_id === selectedContractId}
                className={`flex w-full items-center justify-between rounded-lg border p-3 text-left transition-colors focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-blue-500 ${
                  contract.contract_id === selectedContractId
                    ? 'border-blue-400 bg-blue-50'
                    : 'border-transparent bg-slate-50 hover:bg-slate-100'
                }`}
                onClick={() => setSelectedContract(contract.contract_id)}
              >
                <div>
                  <p className="font-medium text-slate-800">{contract.filename}</p>
                  <p className="text-sm text-slate-500">
                    {new Date(contract.upload_date).toLocaleDateString()} • {contract.model_used}
                    {contract.analysis_completed && contract.risk_level && (
                      <span className={`ml-2 px-2 py-1 rounded text-xs ${
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
                <div className="text-sm text-slate-500">
                  {contract.contract_id === selectedContractId ? 'Selected' : 'Open analysis'}
                </div>
              </button>
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
              onWorkflowUpdate={handleWorkflowUpdate}
              onUploadStart={handleUploadStart}
            />
            
            {/* PDF Processing Workflow */}
            {(workflowStatus?.agent_executions?.length > 0 || isUploading || uploadResult) && (
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
                {workflowStatus && workflowStatus.agent_executions?.length > 0 && (
                  <div className="mb-4">
                    <AgentWorkflowTracker
                      workflowStatus={{
                        ...workflowStatus,
                        agent_executions: workflowStatus.agent_executions.filter((e: any) => e.agent_name !== 'PDF Processing Agent')
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
    </div>
  );
};
