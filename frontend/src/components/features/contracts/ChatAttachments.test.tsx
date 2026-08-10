import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import type { FetchEventSourceInit } from '@microsoft/fetch-event-source';
import { ChatInput } from './input';
import { ChatOutput } from './output';
import { ChatProvider } from './provider';
import { AttachmentUploadError } from '../../../services/chatAttachmentApi';
import { groupStoredMessagesIntoUiMessages } from './sessionMessages';
import { ChatMessage } from './message';

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  fetchEventSource: vi.fn(),
  setSelectedContract: vi.fn(),
  uploadAttachment: vi.fn(),
  fetchImageObjectUrl: vi.fn(),
}));

let mockActiveSession: { session_id: string; contract_id: string | null; title: string; created_at: null; updated_at: null; message_count: number } | null = {
  session_id: 'SESSION_A', contract_id: 'CONTRACT_A', title: 'Payment review',
  created_at: null, updated_at: null, message_count: 2,
};

vi.mock('../../../contexts/ContractHistoryContext', () => ({
  useContractHistory: () => ({
    contracts: [{ contract_id: 'CONTRACT_A', filename: 'Clean_MSA.pdf' }],
    selectedContractId: 'CONTRACT_A',
    setSelectedContract: mocks.setSelectedContract,
  }),
}));

vi.mock('../../../contexts/ChatSessionContext', () => ({
  useChatSession: () => ({
    activeSession: mockActiveSession,
    createSession: mocks.createSession,
  }),
}));

vi.mock('../../../lib/authStore', () => ({
  authHeader: () => 'Bearer test',
  clearSession: vi.fn(),
}));

vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: mocks.fetchEventSource,
}));

vi.mock('../../../services/modelRegistryApi', () => ({
  getWorkflowModels: () => Promise.resolve({
    workflow: 'chat',
    default_model: 'gemini-2.5-flash',
    models: [
      { id: 'gemini-2.5-flash', provider: 'google', display_label: 'Google · Gemini 2.5 Flash', capabilities: ['chat', 'tool_calling', 'streaming', 'vision'] },
      { id: 'mistral-large', provider: 'mistral', display_label: 'Mistral · Large', capabilities: ['chat', 'tool_calling', 'streaming'] },
    ],
  }),
}));

vi.mock('../../../services/chatAttachmentApi', async (importOriginal) => {
  const actual = await importOriginal<typeof import('../../../services/chatAttachmentApi')>();
  return {
    ...actual,
    chatAttachmentApi: {
      upload: mocks.uploadAttachment,
      fetchImageObjectUrl: mocks.fetchImageObjectUrl,
    },
  };
});

function renderChat() {
  return render(
    <ChatProvider>
      <ChatOutput />
      <ChatInput />
    </ChatProvider>
  );
}

function makeFile(name = 'photo.png', type = 'image/png', sizeBytes = 1024) {
  const file = new File(['x'.repeat(sizeBytes)], name, { type });
  return file;
}

function getFileInput(): HTMLInputElement {
  const input = document.querySelector('input[type="file"]');
  if (!input) throw new Error('file input not found');
  return input as HTMLInputElement;
}

describe('Contract Chat image attachments', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mockActiveSession = {
      session_id: 'SESSION_A', contract_id: 'CONTRACT_A', title: 'Payment review',
      created_at: null, updated_at: null, message_count: 2,
    };
    mocks.uploadAttachment.mockResolvedValue({ attachment_id: 'ATTACH_1', mime_type: 'image/png', size_bytes: 1024 });
    mocks.fetchImageObjectUrl.mockResolvedValue('blob:mock-image');
  });

  it('rejects an oversized file client-side without ever calling upload', async () => {
    renderChat();
    const bigFile = makeFile('big.png', 'image/png', 6 * 1024 * 1024);
    fireEvent.change(getFileInput(), { target: { files: [bigFile] } });

    expect(await screen.findByText(/too large/i)).toBeInTheDocument();
    expect(mocks.uploadAttachment).not.toHaveBeenCalled();
  });

  it('rejects an unsupported file type client-side without ever calling upload', async () => {
    renderChat();
    const gif = makeFile('animated.gif', 'image/gif', 1024);
    fireEvent.change(getFileInput(), { target: { files: [gif] } });

    expect(await screen.findByText(/only png, jpeg, or webp/i)).toBeInTheDocument();
    expect(mocks.uploadAttachment).not.toHaveBeenCalled();
  });

  it('shows a loading state during upload, then a ready thumbnail', async () => {
    let resolveUpload!: (value: { attachment_id: string; mime_type: string; size_bytes: number }) => void;
    mocks.uploadAttachment.mockReturnValue(new Promise((resolve) => { resolveUpload = resolve; }));
    renderChat();

    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });

    expect(await screen.findByLabelText('Uploading')).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeDisabled();

    resolveUpload({ attachment_id: 'ATTACH_1', mime_type: 'image/png', size_bytes: 1024 });
    await waitFor(() => expect(screen.queryByLabelText('Uploading')).not.toBeInTheDocument());
  });

  it('surfaces the real server rejection reason on a failed upload and blocks Send', async () => {
    mocks.uploadAttachment.mockRejectedValue(new AttachmentUploadError(400, 'Attachment content is not a supported image format (PNG/JPEG/WEBP)'));
    renderChat();

    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });

    expect(await screen.findByText(/not a supported image format/i)).toBeInTheDocument();
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'What is in this image?' } });
    expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeDisabled();
  });

  it('surfaces a rate-limit rejection distinctly', async () => {
    mocks.uploadAttachment.mockRejectedValue(new AttachmentUploadError(429, 'Too many uploads - please wait a moment and try again.'));
    renderChat();

    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });

    expect(await screen.findByText(/too many uploads/i)).toBeInTheDocument();
  });

  it('removes a pending attachment before sending, allowing Send again', async () => {
    renderChat();
    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });
    await waitFor(() => expect(mocks.uploadAttachment).toHaveBeenCalled());
    await screen.findByRole('button', { name: /Remove attached image/i });

    fireEvent.click(screen.getByRole('button', { name: /Remove attached image/i }));

    expect(screen.queryByRole('button', { name: /Remove attached image/i })).not.toBeInTheDocument();
  });

  it('blocks attaching a 5th image beyond the 4-per-message limit', async () => {
    renderChat();
    const files = [makeFile('a.png'), makeFile('b.png'), makeFile('c.png'), makeFile('d.png')];
    fireEvent.change(getFileInput(), { target: { files } });
    await waitFor(() => expect(mocks.uploadAttachment).toHaveBeenCalledTimes(4));

    fireEvent.change(getFileInput(), { target: { files: [makeFile('e.png')] } });

    expect(await screen.findByText(/up to 4 images/i)).toBeInTheDocument();
    expect(mocks.uploadAttachment).toHaveBeenCalledTimes(4);
  });

  it('warns and blocks Send when a non-vision model is selected with a pending attachment', async () => {
    renderChat();
    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });
    await waitFor(() => expect(screen.queryByLabelText('Uploading')).not.toBeInTheDocument());
    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Describe this image' } });

    fireEvent.click(screen.getByRole('combobox', { name: 'Model' }));
    fireEvent.click(await screen.findByRole('option', { name: 'Mistral · Large' }));

    expect(await screen.findByText(/does not support image attachments/i)).toBeInTheDocument();
    expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeDisabled();
  });

  it('lazily creates a session on first attach when none is active yet', async () => {
    mockActiveSession = null;
    mocks.createSession.mockImplementation(async () => {
      mockActiveSession = {
        session_id: 'SESSION_NEW', contract_id: 'CONTRACT_A', title: 'New chat',
        created_at: null, updated_at: null, message_count: 0,
      };
      return mockActiveSession;
    });
    renderChat();

    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });

    await waitFor(() => expect(mocks.createSession).toHaveBeenCalled());
    await waitFor(() => expect(mocks.uploadAttachment).toHaveBeenCalledWith('SESSION_NEW', expect.any(File)));
  });

  it('sends attachment_ids in the request body and renders the live-sent thumbnail', async () => {
    mocks.fetchEventSource.mockImplementation(async (_url: string, options: FetchEventSourceInit) => {
      await options.onopen?.(new Response(null, { status: 200 }));
      options.onmessage?.({ id: '', event: '', data: JSON.stringify({
        type: 'ai_message', status: 'passed', content: 'I see a blue circle.',
      }) });
      options.onmessage?.({ id: '', event: '', data: JSON.stringify({ type: 'end', status: 'passed', content: '' }) });
    });
    renderChat();

    fireEvent.change(getFileInput(), { target: { files: [makeFile()] } });
    await waitFor(() => expect(screen.queryByLabelText('Uploading')).not.toBeInTheDocument());

    fireEvent.change(screen.getByRole('textbox'), { target: { value: 'What is this?' } });
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
    fireEvent.click(screen.getByRole('button', { name: /Send your prompt now/i }));

    await waitFor(() => expect(mocks.fetchEventSource).toHaveBeenCalled());
    const [, options] = mocks.fetchEventSource.mock.calls[0];
    expect(JSON.parse(options.body)).toMatchObject({
      prompt: 'What is this?',
      attachment_ids: ['ATTACH_1'],
    });

    // The sent message renders its attachment via the real, authenticated
    // fetch path (AttachmentImage -> chatAttachmentApi.fetchImageObjectUrl),
    // not the local preview blob.
    await waitFor(() => expect(mocks.fetchImageObjectUrl).toHaveBeenCalledWith('SESSION_A', 'ATTACH_1'));
    expect(await screen.findByText('I see a blue circle.')).toBeInTheDocument();

    // Composer is cleared for the next turn.
    expect(screen.queryByRole('button', { name: /Remove attached image/i })).not.toBeInTheDocument();
  });

  it('renders attachment thumbnails for a message restored from session history', async () => {
    const restored = groupStoredMessagesIntoUiMessages([{
      message_id: 'm1', role: 'user_message', content: 'What is in this image?',
      model: null, tool_name: null, tool_call_id: null,
      citations: [], attachments: [{ attachment_id: 'ATTACH_RESTORED', mime_type: 'image/png' }],
      sequence: 1, created_at: '2026-08-10T00:00:00Z',
    }]);

    render(
      <ChatProvider>
        <ChatMessage message={restored[0]} />
      </ChatProvider>
    );

    await waitFor(() => expect(mocks.fetchImageObjectUrl).toHaveBeenCalledWith('SESSION_A', 'ATTACH_RESTORED'));
    expect(await screen.findByRole('button', { name: 'Open attached image' })).toBeInTheDocument();
  });
});
