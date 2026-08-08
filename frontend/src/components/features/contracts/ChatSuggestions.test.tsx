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
      session_id: 'SESSION_NEW', contract_id: null, title: 'how many SOW contracts?',
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

    fireEvent.click(screen.getByRole('button', { name: 'how many SOW contracts?' }));

    await waitFor(() => expect(mocks.createSession).toHaveBeenCalledWith(
      null,
      'how many SOW contracts?'
    ));
    await waitFor(() => expect(mocks.fetchEventSource).toHaveBeenCalledTimes(1));
    const request = mocks.fetchEventSource.mock.calls[0][1];
    expect(JSON.parse(request.body)).toMatchObject({
      prompt: 'how many SOW contracts?',
      contract_id: null,
      session_id: 'SESSION_NEW',
    });
  });
});
