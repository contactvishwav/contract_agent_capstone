import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { SessionSwitcher } from './SessionSwitcher';

const mocks = vi.hoisted(() => ({
  getSessionDetail: vi.fn(),
  selectSession: vi.fn(),
  startNewSession: vi.fn(),
  refreshSessions: vi.fn(),
  replaceMessages: vi.fn(),
  reset: vi.fn(),
  setSelectedContract: vi.fn(),
}));

const active = {
  session_id: 'SESSION_A', contract_id: 'CONTRACT_A', title: 'Fee review',
  created_at: null, updated_at: null, message_count: 2,
};
const second = {
  session_id: 'SESSION_B', contract_id: 'CONTRACT_A', title: 'Risk review',
  created_at: null, updated_at: null, message_count: 0,
};

vi.mock('../../../contexts/ChatSessionContext', () => ({
  useChatSession: () => ({
    sessions: [active, second],
    activeSession: active,
    isLoadingSessions: false,
    sessionListError: null,
    refreshSessions: mocks.refreshSessions,
    selectSession: mocks.selectSession,
    startNewSession: mocks.startNewSession,
  }),
}));
vi.mock('../../../contexts/ContractHistoryContext', () => ({
  useContractHistory: () => ({
    contracts: [{ contract_id: 'CONTRACT_A', filename: 'Clean_SOW.pdf' }],
    setSelectedContract: mocks.setSelectedContract,
  }),
}));
vi.mock('./provider', () => ({
  useChat: () => ({ replaceMessages: mocks.replaceMessages, reset: mocks.reset }),
}));
vi.mock('../../../services/chatSessionApi', async () => {
  const actual = await vi.importActual<typeof import('../../../services/chatSessionApi')>('../../../services/chatSessionApi');
  return { ...actual, chatSessionApi: { getSessionDetail: mocks.getSessionDetail } };
});

describe('SessionSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    mocks.getSessionDetail.mockResolvedValue({ ...active, messages: [] });
  });

  it('restores persisted messages and lets the already-active row retry', async () => {
    render(<SessionSwitcher />);
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalledWith('SESSION_A'));
    expect(mocks.replaceMessages).toHaveBeenCalledWith([]);

    fireEvent.click(screen.getByRole('button', { name: 'Fee review Clean_SOW.pdf' }));
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalledTimes(2));
  });

  it('loads another session and starts a clean new chat explicitly', async () => {
    render(<SessionSwitcher />);
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalled());

    mocks.getSessionDetail.mockResolvedValueOnce({ ...second, messages: [] });
    fireEvent.click(screen.getByRole('button', { name: 'Risk review Clean_SOW.pdf' }));
    await waitFor(() => expect(mocks.selectSession).toHaveBeenCalledWith(second));

    fireEvent.click(screen.getByRole('button', { name: 'New chat' }));
    expect(mocks.startNewSession).toHaveBeenCalled();
    expect(mocks.reset).toHaveBeenCalled();
  });

  it('surfaces session-load errors instead of swallowing them', async () => {
    mocks.getSessionDetail.mockRejectedValue(new Error('offline'));
    render(<SessionSwitcher />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this conversation');
  });
});
