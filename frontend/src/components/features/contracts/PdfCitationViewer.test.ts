import React from 'react';
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { ChatCitation } from '../../../services/chatSessionApi';
import { uniqueHighlightItemIndexes } from './pdfHighlight';

const mocks = vi.hoisted(() => ({
  apiFetch: vi.fn(),
  destroy: vi.fn(),
  getDocument: vi.fn(),
}));

vi.mock('../../../lib/apiClient', () => ({ apiFetch: mocks.apiFetch }));
vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({ default: '/pdf-worker.mjs' }));
vi.mock('pdfjs-dist', () => ({
  GlobalWorkerOptions: {},
  Util: { transform: (_viewport: number[], item: number[]) => item },
  getDocument: mocks.getDocument,
}));

import { PdfCitationViewer } from './PdfCitationViewer';

const citation: ChatCitation = {
  citation_id: 'CIT_1',
  contract_id: 'CONTRACT_A',
  filename: 'Clean_MSA.pdf',
  source_type: 'chunk',
  section_id: null,
  section_title: null,
  clause_id: null,
  clause_type: null,
  chunk_id: 'CHUNK_1',
  chunk_index: 0,
  start_offset: null,
  end_offset: null,
  tool_name: 'EnhancedContractSearch',
  tool_call_id: 'call_1',
  validation_status: 'tenant_active',
  source_available: true,
  page: 2,
  excerpt: 'Payment is due within 90 days.',
  highlight_text: 'Payment is due within 90 days.',
  provenance_status: 'exact',
};

beforeEach(() => {
  vi.clearAllMocks();
  vi.spyOn(HTMLCanvasElement.prototype, 'getContext').mockReturnValue({} as CanvasRenderingContext2D);
  mocks.apiFetch.mockResolvedValue(new Response(btoa('%PDF source'), {
    status: 200,
    headers: { 'Content-Type': 'application/pdf' },
  }));
  mocks.getDocument.mockReturnValue({ promise: Promise.resolve({
    numPages: 3,
    destroy: mocks.destroy,
    getPage: vi.fn().mockResolvedValue({
      getViewport: () => ({ width: 600, height: 800, transform: [1, 0, 0, 1, 0, 0] }),
      render: () => ({ cancel: vi.fn(), promise: Promise.resolve() }),
      getTextContent: () => Promise.resolve({ items: [
        { str: 'Payment', transform: [1, 0, 0, 12, 20, 40], width: 50 },
        { str: 'is due within 90 days.', transform: [1, 0, 0, 12, 75, 40], width: 150 },
      ] }),
    }),
  }) });
});

describe('PDF citation exact highlight matching', () => {
  it('matches one whitespace-normalized passage across text-layer items', () => {
    expect([...uniqueHighlightItemIndexes(
      ['Payment', 'is due', 'within 90 days.'],
      'Payment\n is due   within 90 days.',
    )]).toEqual([0, 1, 2]);
  });

  it('ignores empty PDF.js line-break items without shifting the match', () => {
    expect([...uniqueHighlightItemIndexes(
      ['Payment Terms', '', 'Payment is due within 90 days.'],
      'Payment Terms Payment is due within 90 days.',
    )]).toEqual([0, 2]);
  });

  it('does not highlight an ambiguous repeated passage', () => {
    expect(uniqueHighlightItemIndexes(
      ['Payment is due within 90 days.', 'Payment is due within 90 days.'],
      'Payment is due within 90 days.',
    ).size).toBe(0);
  });

  it('does not claim exact highlighting for a very short locator', () => {
    expect(uniqueHighlightItemIndexes(['Net 30'], 'Net 30').size).toBe(0);
  });
});

describe('authenticated PDF citation viewer', () => {
  it('loads a tenant-authorized relative endpoint, navigates to the verified page, and closes with Escape', async () => {
    const onClose = vi.fn();
    render(React.createElement(PdfCitationViewer, { citation, onClose }));

    expect(screen.getByRole('dialog')).toHaveAccessibleName('Source: Clean_MSA.pdf, page 2');
    await waitFor(() => expect(mocks.apiFetch).toHaveBeenCalledWith(
      '/api/documents/CONTRACT_A/source',
      { cache: 'no-store' },
    ));
    expect(await screen.findByText('2 / 3')).toBeInTheDocument();
    await waitFor(() => expect(document.querySelectorAll('.bg-yellow-300\\/70')).toHaveLength(2));
    expect(mocks.getDocument).toHaveBeenCalledWith(expect.objectContaining({
      data: expect.any(Uint8Array),
      standardFontDataUrl: '/pdfjs/standard_fonts/',
      cMapUrl: '/pdfjs/cmaps/',
      cMapPacked: true,
    }));

    fireEvent.keyDown(window, { key: 'Escape' });
    expect(onClose).toHaveBeenCalledTimes(1);
  });

  it('shows a non-disclosing unavailable state for a denied source', async () => {
    mocks.apiFetch.mockResolvedValueOnce(new Response(null, { status: 404 }));
    render(React.createElement(PdfCitationViewer, { citation, onClose: vi.fn() }));

    expect(await screen.findByRole('alert')).toHaveTextContent(
      'This source is unavailable or you no longer have access.',
    );
    expect(mocks.getDocument).not.toHaveBeenCalled();
  });
});
