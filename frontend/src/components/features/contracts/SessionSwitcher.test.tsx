import { StrictMode } from 'react';
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
  renameSession: vi.fn(),
  stopActiveRequest: vi.fn(),
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
    renameSession: mocks.renameSession,
  }),
}));
vi.mock('../../../contexts/ContractHistoryContext', () => ({
  useContractHistory: () => ({
    contracts: [{ contract_id: 'CONTRACT_A', filename: 'Clean_SOW.pdf' }],
    setSelectedContract: mocks.setSelectedContract,
  }),
}));
vi.mock('./provider', () => ({
  useChat: () => ({
    replaceMessages: mocks.replaceMessages,
    reset: mocks.reset,
    stopActiveRequest: mocks.stopActiveRequest,
  }),
}));
vi.mock('../../../services/chatSessionApi', async () => {
  const actual = await vi.importActual<typeof import('../../../services/chatSessionApi')>('../../../services/chatSessionApi');
  return { ...actual, chatSessionApi: { getSessionDetail: mocks.getSessionDetail } };
});

describe('SessionSwitcher', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    active.message_count = 2;
    mocks.getSessionDetail.mockResolvedValue({ ...active, messages: [] });
    mocks.stopActiveRequest.mockResolvedValue(undefined);
  });

  it('does not replace a newly-created empty session while its first turn streams', () => {
    active.message_count = 0;
    render(<SessionSwitcher />);
    expect(mocks.getSessionDetail).not.toHaveBeenCalled();
    expect(mocks.replaceMessages).not.toHaveBeenCalled();
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
    await waitFor(() => expect(mocks.startNewSession).toHaveBeenCalled());
    expect(mocks.stopActiveRequest).toHaveBeenCalled();
    expect(mocks.reset).toHaveBeenCalled();
  });

  it('waits for active-stream cleanup before loading another session', async () => {
    let releaseCancellation: () => void = () => undefined;
    mocks.stopActiveRequest.mockImplementationOnce(() => new Promise<void>((resolve) => {
      releaseCancellation = resolve;
    }));
    render(<SessionSwitcher />);
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalledWith('SESSION_A'));
    vi.clearAllMocks();
    mocks.getSessionDetail.mockResolvedValue({ ...second, messages: [] });

    fireEvent.click(screen.getByRole('button', { name: 'Risk review Clean_SOW.pdf' }));
    expect(mocks.stopActiveRequest).toHaveBeenCalledTimes(1);
    expect(mocks.getSessionDetail).not.toHaveBeenCalled();

    releaseCancellation();
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalledWith('SESSION_B'));
  });

  it('does not double-fetch session detail under React StrictMode\'s dev-mode double effect invoke', async () => {
    // Real, confirmed bug found live while investigating a manually-reported
    // "attachment doesn't render after refresh" report: the auto-restore
    // effect below only recorded loadedSessionId.current *inside*
    // loadSession's async success path, so StrictMode's mount->cleanup->
    // mount-again dev-mode simulation (this app runs <StrictMode> - see
    // main.tsx) could fire two concurrent getSessionDetail calls for the
    // same session before the first one's ref write ever landed. Not
    // confirmed as that report's root cause, but a genuine wasted-request
    // race closed regardless - the ref must now be set synchronously,
    // before the async call starts.
    render(<StrictMode><SessionSwitcher /></StrictMode>);
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalledWith('SESSION_A'));
    // Give any wrongly-duplicated second call a chance to fire before asserting.
    await new Promise((resolve) => setTimeout(resolve, 10));
    expect(mocks.getSessionDetail).toHaveBeenCalledTimes(1);
  });

  it('surfaces session-load errors instead of swallowing them', async () => {
    mocks.getSessionDetail.mockRejectedValue(new Error('offline'));
    render(<SessionSwitcher />);
    expect(await screen.findByRole('alert')).toHaveTextContent('Could not load this conversation');
  });

  it('renames inline without selecting or submitting the conversation', async () => {
    mocks.renameSession.mockResolvedValue({ ...active, title: 'Payment obligations' });
    render(<SessionSwitcher />);
    await waitFor(() => expect(mocks.getSessionDetail).toHaveBeenCalled());
    vi.clearAllMocks();

    fireEvent.click(screen.getByRole('button', { name: 'Rename Fee review' }));
    const input = screen.getByRole('textbox', { name: 'Conversation name' });
    fireEvent.change(input, { target: { value: '  Payment obligations  ' } });
    fireEvent.keyDown(input, { key: 'Enter' });

    await waitFor(() => expect(mocks.renameSession).toHaveBeenCalledWith('SESSION_A', 'Payment obligations'));
    expect(mocks.selectSession).not.toHaveBeenCalled();
  });
});
