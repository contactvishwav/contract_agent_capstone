import { fireEvent, render, screen } from '@testing-library/react';
import { describe, expect, it, vi, beforeEach } from 'vitest';
import { DocumentUpload } from './DocumentUpload';

// Real bug found live in production: DocumentUpload had no signal for
// "the parent's model-registry fetch is still in flight," so an upload
// attempted before it resolved sent model= (empty) straight to the
// backend and got a real 400 back (POST /api/documents/enhanced/upload
// ?model=&enable_embeddings=true). These tests reproduce that race at
// the component level and assert the fix: no network call is ever made
// with an unresolved model, regardless of whether a caller bypasses the
// disabled dropzone/file-input (e.g. a stray programmatic file-input
// change event).

const mocks = vi.hoisted(() => ({
  uploadEnhancedDocument: vi.fn(),
  apiFetch: vi.fn(),
}));

vi.mock('../../../services/enhancedSearchApi', () => ({
  enhancedSearchApi: { uploadEnhancedDocument: mocks.uploadEnhancedDocument },
}));

vi.mock('../../../lib/apiClient', () => ({
  apiFetch: mocks.apiFetch,
}));

function makeFile(name = 'contract.pdf') {
  return new File(['%PDF-1.4 fake contract content'], name, { type: 'application/pdf' });
}

function selectFile(file: File) {
  const input = document.getElementById('file-input') as HTMLInputElement;
  fireEvent.change(input, { target: { files: [file] } });
}

beforeEach(() => {
  mocks.uploadEnhancedDocument.mockReset();
  mocks.apiFetch.mockReset();
  mocks.apiFetch.mockResolvedValue({ ok: false });
  mocks.uploadEnhancedDocument.mockResolvedValue({
    filename: 'contract.pdf',
    status: 'success',
    contract_id: 'UPLOADED_TEST',
    model_used: 'gemini-2.5-flash',
    enhanced_embeddings: true,
  });
});

describe('DocumentUpload — model-registry race condition', () => {
  it('blocks upload and shows a loading state while modelsLoading is true, even if the file input fires', () => {
    render(<DocumentUpload modelSelection={undefined} modelsLoading={true} modelError={null} />);

    expect(screen.getByText(/loading available models/i)).toBeInTheDocument();
    const input = document.getElementById('file-input') as HTMLInputElement;
    expect(input).toBeDisabled();

    selectFile(makeFile());

    expect(mocks.uploadEnhancedDocument).not.toHaveBeenCalled();
    // The defensive guard in handleFiles still reports an honest result
    // even though the input is disabled - covers a bypass, not just the
    // visible UI state.
    expect(screen.getByText(/no analysis model is available yet/i)).toBeInTheDocument();
  });

  it('blocks upload and shows an honest error state when the registry fetch failed', () => {
    render(<DocumentUpload modelSelection={undefined} modelsLoading={false} modelError="Available analysis models could not be loaded." />);

    expect(screen.getByRole('alert')).toHaveTextContent('Available analysis models could not be loaded.');
    const input = document.getElementById('file-input') as HTMLInputElement;
    expect(input).toBeDisabled();

    selectFile(makeFile());

    expect(mocks.uploadEnhancedDocument).not.toHaveBeenCalled();
    // Appears twice by design: once in the dropzone's own error state,
    // once in the post-attempt result panel from the defensive guard.
    expect(screen.getAllByText(/Available analysis models could not be loaded\./).length).toBeGreaterThanOrEqual(2);
  });

  it('blocks upload if modelsLoading/modelError are both falsy but no model actually resolved (empty string)', () => {
    render(<DocumentUpload modelSelection="" modelsLoading={false} modelError={null} />);

    const input = document.getElementById('file-input') as HTMLInputElement;
    expect(input).toBeDisabled();

    selectFile(makeFile());

    expect(mocks.uploadEnhancedDocument).not.toHaveBeenCalled();
  });

  it('proceeds normally once a real model has resolved (no regression)', async () => {
    render(<DocumentUpload modelSelection="gemini-2.5-flash" modelsLoading={false} modelError={null} />);

    expect(screen.getByText(/using model:/i)).toBeInTheDocument();
    const input = document.getElementById('file-input') as HTMLInputElement;
    expect(input).not.toBeDisabled();

    selectFile(makeFile());

    await vi.waitFor(() => {
      expect(mocks.uploadEnhancedDocument).toHaveBeenCalledTimes(1);
    });
    expect(mocks.uploadEnhancedDocument).toHaveBeenCalledWith(expect.any(File), 'gemini-2.5-flash', true);
  });

  it('treats missing modelsLoading/modelError props (undefined) as ready=false when modelSelection is also unresolved, never as "assume a default model"', () => {
    render(<DocumentUpload modelSelection={undefined} />);

    const input = document.getElementById('file-input') as HTMLInputElement;
    expect(input).toBeDisabled();

    selectFile(makeFile());

    expect(mocks.uploadEnhancedDocument).not.toHaveBeenCalled();
  });
});
