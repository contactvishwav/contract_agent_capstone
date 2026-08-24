import React, { useState, useCallback } from 'react';
import { Card } from '../../shared/ui/card';
import { Loader } from '../../shared/ui/loader';
import { apiFetch } from '../../../lib/apiClient';
import { enhancedSearchApi } from '../../../services/enhancedSearchApi';

interface DocumentUploadProps {
  onUploadComplete?: (result: UploadResult) => void;
  modelSelection?: string;
  // Real bug found live in production: with no way to know the parent's
  // model-registry fetch was still in flight, this component let a user
  // upload before modelSelection was populated, sending model= (empty) to
  // the backend and getting a real 400 back. modelsLoading/modelError are
  // the missing "is a real model actually resolved yet" signal from the
  // parent - both optional so existing callers/tests that don't pass them
  // keep working, but plain `undefined` is treated as "not loading, no
  // error" (see modelReady below), never as "assume a default model."
  modelsLoading?: boolean;
  modelError?: string | null;
  onWorkflowUpdate?: (status: any) => void;
  onUploadStart?: () => void;
}

interface UploadResult {
  filename: string;
  status: string;
  contract_id?: string;
  existing_contract_id?: string;
  details: string;
  model_used: string;
  enhanced_embeddings?: boolean;
}

export const DocumentUpload: React.FC<DocumentUploadProps> = ({
  onUploadComplete,
  modelSelection,
  modelsLoading = false,
  modelError = null,
  onWorkflowUpdate,
  onUploadStart
}) => {
  const [isUploading, setIsUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [enableEnhanced, setEnableEnhanced] = useState(true);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);

  // The single source of truth for "is it safe to call the upload API
  // right now" - deliberately not a hardcoded fallback model string (the
  // bug this replaces was exactly that kind of silent workaround, just
  // implicit via a default parameter instead of an explicit one). Ready
  // only once the parent's registry fetch has resolved, produced no
  // error, and actually resolved a non-empty model id.
  const modelReady = !modelsLoading && !modelError && !!modelSelection;

  const handleFiles = useCallback(async (files: FileList) => {
    const file = files[0];
    if (!file) return;

    // Defense in depth: even if the dropzone/file-input gating below is
    // ever bypassed (programmatic file drop, future refactor), never send
    // a request with an unresolved model - fail with an honest, visible
    // status instead of a silent empty model= hitting the backend. The
    // `!modelSelection` check (rather than trusting the `modelReady`
    // constant above) also narrows modelSelection to `string` for the
    // rest of this function, so the calls below never pass `undefined`
    // into a function with its own hardcoded default parameter.
    if (modelsLoading || modelError || !modelSelection) {
      setUploadResult({
        filename: file.name,
        status: 'error',
        details: modelError || 'No analysis model is available yet - please wait for models to finish loading and try again.',
        model_used: modelSelection || ''
      });
      return;
    }

    // Validate file type
    if (!file.name.toLowerCase().endsWith('.pdf')) {
      alert('Please select a PDF file');
      return;
    }

    // Validate file size (50MB)
    if (file.size > 50 * 1024 * 1024) {
      alert('File too large. Maximum size is 50MB');
      return;
    }

    setIsUploading(true);
    setUploadResult(null);
    onUploadStart?.();

    // Start polling for workflow status
    const pollWorkflow = setInterval(async () => {
      try {
        const workflowResponse = await apiFetch('/api/workflow/status');
        if (workflowResponse.ok) {
          const workflowData = await workflowResponse.json();
          onWorkflowUpdate?.(workflowData);
        }
      } catch (e) {
        // Ignore workflow polling errors
      }
    }, 500);

    try {
      let result: UploadResult;
      if (enableEnhanced) {
        const enhancedData = await enhancedSearchApi.uploadEnhancedDocument(file, modelSelection, true);
        result = {
          filename: enhancedData.filename || file.name,
          status: enhancedData.status || 'success',
          contract_id: enhancedData.contract_id,
          existing_contract_id: enhancedData.existing_contract_id,
          details: enhancedData.details || enhancedData.message || 'Enhanced multi-level embeddings generated',
          model_used: enhancedData.model_used || modelSelection,
          enhanced_embeddings: enhancedData.enhanced_embeddings ?? true
        };
      } else {
        const formData = new FormData();
        formData.append('file', file);
        formData.append('model', modelSelection);

        const response = await apiFetch('/api/documents/upload', {
          method: 'POST',
          body: formData
        });

        if (!response.ok) {
          const errorText = await response.text();
          throw new Error(`Upload failed: ${response.status} - ${errorText}`);
        }

        const responseText = await response.text();

        try {
          result = JSON.parse(responseText);
        } catch (parseError) {
          throw new Error(`Invalid response format: ${responseText.substring(0, 100)}`);
        }
      }

      setUploadResult(result);

      if (onUploadComplete) {
        onUploadComplete(result);
      }

      // Final workflow status update
      setTimeout(async () => {
        try {
          const workflowResponse = await apiFetch('/api/workflow/status');
          if (workflowResponse.ok) {
            const workflowData = await workflowResponse.json();
            onWorkflowUpdate?.(workflowData);
          }
        } catch (e) {
          // Ignore final workflow polling error
        }
      }, 1000);

    } catch (error) {
      setUploadResult({
        filename: file.name,
        status: 'error',
        details: error instanceof Error ? error.message : 'Upload failed',
        model_used: modelSelection
      });
    } finally {
      clearInterval(pollWorkflow);
      setIsUploading(false);
    }
  }, [enableEnhanced, modelSelection, onUploadComplete, onWorkflowUpdate, onUploadStart]);

  const handleDrag = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    if (e.type === "dragenter" || e.type === "dragover") {
      setDragActive(true);
    } else if (e.type === "dragleave") {
      setDragActive(false);
    }
  }, []);

  const handleDrop = useCallback((e: React.DragEvent) => {
    e.preventDefault();
    e.stopPropagation();
    setDragActive(false);

    if (e.dataTransfer.files && e.dataTransfer.files[0]) {
      handleFiles(e.dataTransfer.files);
    }
  }, [handleFiles]);

  const handleFileInput = useCallback((e: React.ChangeEvent<HTMLInputElement>) => {
    if (e.target.files && e.target.files[0]) {
      handleFiles(e.target.files);
    }
  }, [handleFiles]);

  const getStatusColor = (status: string) => {
    switch (status) {
      case 'success': return 'text-green-600';
      case 'error': return 'text-red-600';
      case 'review_required': return 'text-yellow-600';
      case 'skipped': return 'text-gray-600';
      case 'duplicate': return 'text-blue-600';
      default: return 'text-blue-600';
    }
  };

  const getStatusMessage = (result: UploadResult) => {
    const enhancedTag = result.enhanced_embeddings ? ' (Multi-level Embeddings Active)' : '';
    switch (result.status) {
      case 'success':
        return `✅ Contract created successfully${enhancedTag}! ID: ${result.contract_id}`;
      case 'error':
        return `❌ Processing failed: ${result.details}`;
      case 'review_required':
        return `⚠️ Manual review required: ${result.details}`;
      case 'skipped':
        return `ℹ️ Document skipped: ${result.details}`;
      case 'duplicate':
        return `ℹ️ Already uploaded - showing the existing contract (ID: ${result.contract_id})${enhancedTag}`;
      default:
        return `📄 Processing completed: ${result.details}`;
    }
  };

  return (
    <div className="w-full max-w-md mx-auto">
      <Card className="p-6">
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Upload PDF Contract</h3>

          {/* Enhanced Multi-Level Embeddings Toggle */}
          <div className="flex items-center justify-between p-3 bg-slate-50 border border-slate-200 rounded-lg">
            <div className="space-y-0.5 pr-2">
              <label htmlFor="enhanced-upload-toggle" className="text-sm font-semibold text-slate-800 cursor-pointer">
                Multi-Level Embeddings
              </label>
              <p className="text-xs text-slate-500">
                Generate document, section, clause & relationship embeddings
              </p>
            </div>
            <input
              id="enhanced-upload-toggle"
              type="checkbox"
              checked={enableEnhanced}
              onChange={(e) => setEnableEnhanced(e.target.checked)}
              className="h-4 w-4 rounded border-slate-300 text-blue-600 focus:ring-blue-500 cursor-pointer"
              disabled={isUploading || !modelReady}
            />
          </div>

          {/* Upload Area - disabled (not just visually, but non-interactive)
              until a real model has actually resolved. Bypassing this via
              drag-drop still hits handleFiles' own modelReady guard above,
              so there's no path from "not ready" to a real API call. */}
          <div
            className={`
              border-2 border-dashed rounded-lg p-8 text-center transition-colors
              ${!modelReady ? 'cursor-not-allowed opacity-50 border-gray-200' : 'cursor-pointer'}
              ${dragActive && modelReady ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}
              ${isUploading ? 'pointer-events-none opacity-50' : ''}
            `}
            onDragEnter={modelReady ? handleDrag : undefined}
            onDragLeave={modelReady ? handleDrag : undefined}
            onDragOver={modelReady ? handleDrag : undefined}
            onDrop={modelReady ? handleDrop : undefined}
            onClick={modelReady ? () => document.getElementById('file-input')?.click() : undefined}
            aria-disabled={!modelReady}
          >
            {isUploading ? (
              <div className="space-y-2">
                <Loader className="mx-auto" />
                <p className="text-sm text-gray-600">
                  {enableEnhanced ? 'Processing PDF with Multi-Level Embeddings...' : 'Processing PDF...'}
                </p>
              </div>
            ) : modelsLoading ? (
              <div className="space-y-2">
                <Loader className="mx-auto" />
                <p className="text-sm text-gray-600">Loading available models…</p>
              </div>
            ) : modelError ? (
              <div className="space-y-2">
                <div className="text-4xl">⚠️</div>
                <p className="text-sm font-medium text-red-700" role="alert">
                  Upload unavailable: {modelError}
                </p>
              </div>
            ) : !modelSelection ? (
              <div className="space-y-2">
                <div className="text-4xl">⚠️</div>
                <p className="text-sm font-medium text-red-700" role="alert">
                  Upload unavailable: no analysis model is currently selected.
                </p>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="text-4xl">📄</div>
                <p className="text-sm font-medium">
                  Drop PDF here or click to browse
                </p>
                <p className="text-xs text-gray-500">
                  Maximum file size: 50MB
                </p>
              </div>
            )}
          </div>

          <input
            id="file-input"
            type="file"
            accept=".pdf"
            onChange={handleFileInput}
            className="hidden"
            disabled={isUploading || !modelReady}
          />

          {/* Model Selection Display */}
          <div className="text-sm text-gray-600">
            {modelReady
              ? <>Using model: <span className="font-medium">{modelSelection}</span></>
              : <span className="text-gray-400">Model not yet available</span>}
          </div>

          {/* Upload Result */}
          {uploadResult && (
            <div className={`p-3 rounded-lg border ${getStatusColor(uploadResult.status)}`}>
              <p className="text-sm font-medium">
                {uploadResult.filename}
              </p>
              <p className="text-xs mt-1">
                {getStatusMessage(uploadResult)}
              </p>
            </div>
          )}
        </div>
      </Card>
    </div>
  );
};