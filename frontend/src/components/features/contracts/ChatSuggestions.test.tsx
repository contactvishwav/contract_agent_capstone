import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatProvider } from './provider';
import { ChatOutput } from './output';
import { ChatInput } from './input';

const mocks = vi.hoisted(() => ({
  createSession: vi.fn(),
  fetchEventSource: vi.fn(),
  setSelectedContract: vi.fn(),
}));

vi.mock('../../../contexts/ContractHistoryContext', () => ({
  useContractHistory: () => ({
    contracts: [],
    selectedContractId: null,
    setSelectedContract: mocks.setSelectedContract,
  }),
}));
vi.mock('../../../contexts/ChatSessionContext', () => ({
  useChatSession: () => ({ activeSession: null, createSession: mocks.createSession }),
}));
vi.mock('../../../lib/authStore', () => ({ authHeader: () => 'Bearer test', clearSession: vi.fn() }));
vi.mock('@microsoft/fetch-event-source', () => ({
  fetchEventSource: mocks.fetchEventSource,
}));

describe('Contract Chat suggestions', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.createSession.mockResolvedValue({
      session_id: 'SESSION_NEW', contract_id: null, title: 'How many SOW contracts?',
      created_at: null, updated_at: null, message_count: 0,
    });
    mocks.fetchEventSource.mockImplementation(async (_url, options) => {
      await options.onopen?.(new Response(null, { status: 200 }));
      options.onmessage?.({ data: JSON.stringify({ type: 'end', content: '' }) });
      options.onclose?.();
    });
  });

  it('submits a suggestion as a real new-session prompt', async () => {
    render(
      <ChatProvider>
        <ChatOutput />
        <ChatInput />
      </ChatProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'How many SOW contracts?' }));

    await waitFor(() => expect(mocks.createSession).toHaveBeenCalledWith(
      null,
      'How many SOW contracts?'
    ));
    await waitFor(() => expect(mocks.fetchEventSource).toHaveBeenCalledTimes(1));
    const request = mocks.fetchEventSource.mock.calls[0][1];
    expect(JSON.parse(request.body)).toMatchObject({
      prompt: 'How many SOW contracts?',
      contract_id: null,
      session_id: 'SESSION_NEW',
    });
  });

  it('shows an explicit validator failure and restores the composer after end', async () => {
    mocks.fetchEventSource.mockImplementationOnce(async (_url, options) => {
      await options.onopen?.(new Response(null, { status: 200 }));
      options.onmessage?.({ data: JSON.stringify({
        type: 'error', status: 'validation_failed',
        content: 'Response validation failed. Please retry.',
      }) });
      options.onmessage?.({ data: JSON.stringify({
        type: 'end', status: 'validation_failed', content: '',
      }) });
      options.onclose?.();
    });
    render(
      <ChatProvider>
        <ChatOutput />
        <ChatInput />
      </ChatProvider>
    );

    fireEvent.click(screen.getByRole('button', { name: 'How many SOW contracts?' }));

    expect(await screen.findByRole('alert')).toHaveTextContent('Response validation failed');
    await waitFor(() => expect(screen.getByRole('button', { name: /Send your prompt now/i })).toBeEnabled());
  });
});
