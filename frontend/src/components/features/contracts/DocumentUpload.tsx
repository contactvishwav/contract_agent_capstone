import React, { useState, useCallback } from 'react';
import { Card } from '../../shared/ui/card';
import { Loader } from '../../shared/ui/loader';
import { apiFetch } from '../../../lib/apiClient';

interface DocumentUploadProps {
  onUploadComplete?: (result: UploadResult) => void;
  modelSelection?: string;
  onWorkflowUpdate?: (status: any) => void;
  onUploadStart?: () => void;
}

interface UploadResult {
  filename: string;
  status: string;
  contract_id?: string;
  details: string;
  model_used: string;
}

export const DocumentUpload: React.FC<DocumentUploadProps> = ({
  onUploadComplete,
  modelSelection = "gemini-2.5-flash",
  onWorkflowUpdate,
  onUploadStart
}) => {
  const [isUploading, setIsUploading] = useState(false);
  const [dragActive, setDragActive] = useState(false);
  const [uploadResult, setUploadResult] = useState<UploadResult | null>(null);

  const handleFiles = useCallback(async (files: FileList) => {
    const file = files[0];
    if (!file) return;

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
      const formData = new FormData();
      formData.append('file', file);
      formData.append('model', modelSelection);

      const response = await apiFetch('/api/documents/upload', {
        method: 'POST',
        body: formData
      });

      if (!response.ok) {
        const errorText = await response.text();
        // removed console error
        throw new Error(`Upload failed: ${response.status} - ${errorText}`);
      }

      const responseText = await response.text();
      // removed console log

      let result: UploadResult;
      try {
        result = JSON.parse(responseText);
      } catch (parseError) {
        // removed console error
        throw new Error(`Invalid response format: ${responseText.substring(0, 100)}`);
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
      // removed console error
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
  }, [modelSelection, onUploadComplete]);

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
      default: return 'text-blue-600';
    }
  };

  const getStatusMessage = (result: UploadResult) => {
    switch (result.status) {
      case 'success':
        return `✅ Contract created successfully! ID: ${result.contract_id}`;
      case 'error':
        return `❌ Processing failed: ${result.details}`;
      case 'review_required':
        return `⚠️ Manual review required: ${result.details}`;
      case 'skipped':
        return `ℹ️ Document skipped: ${result.details}`;
      default:
        return `📄 Processing completed: ${result.details}`;
    }
  };

  return (
    <div className="w-full max-w-md mx-auto">
      <Card className="p-6">
        <div className="space-y-4">
          <h3 className="text-lg font-semibold">Upload PDF Contract</h3>

          {/* Upload Area */}
          <div
            className={`
              border-2 border-dashed rounded-lg p-8 text-center cursor-pointer transition-colors
              ${dragActive ? 'border-blue-500 bg-blue-50' : 'border-gray-300 hover:border-gray-400'}
              ${isUploading ? 'pointer-events-none opacity-50' : ''}
            `}
            onDragEnter={handleDrag}
            onDragLeave={handleDrag}
            onDragOver={handleDrag}
            onDrop={handleDrop}
            onClick={() => document.getElementById('file-input')?.click()}
          >
            {isUploading ? (
              <div className="space-y-2">
                <Loader className="mx-auto" />
                <p className="text-sm text-gray-600">Processing PDF...</p>
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
            disabled={isUploading}
          />

          {/* Model Selection Display */}
          <div className="text-sm text-gray-600">
            Using model: <span className="font-medium">{modelSelection}</span>
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