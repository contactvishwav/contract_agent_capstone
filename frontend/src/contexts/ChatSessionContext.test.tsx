import { act, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';
import { ChatSessionProvider, useChatSession } from './ChatSessionContext';

const mocks = vi.hoisted(() => ({
  tenantId: 'tenant-a',
  listSessions: vi.fn(),
}));

vi.mock('./AuthContext', () => ({
  useAuth: () => ({ session: { tenantId: mocks.tenantId } }),
}));
vi.mock('../services/chatSessionApi', () => ({
  chatSessionApi: {
    listSessions: mocks.listSessions,
    createSession: vi.fn(),
  },
}));

const tenantASession = {
  session_id: 'SESSION_A', contract_id: 'CONTRACT_A', title: 'Tenant A chat',
  created_at: null, updated_at: null, message_count: 1,
};

function Probe() {
  const state = useChatSession();
  return <div>{state.sessions.map((session) => session.title).join(',')}|{state.activeSession?.title || 'none'}</div>;
}

describe('ChatSessionProvider tenant lifecycle', () => {
  beforeEach(() => {
    vi.clearAllMocks();
    localStorage.clear();
    mocks.tenantId = 'tenant-a';
    mocks.listSessions.mockResolvedValue([tenantASession]);
  });

  it('uses a tenant-scoped restore key and clears state when the authenticated tenant changes', async () => {
    localStorage.setItem('chat_last_active_session_id:tenant-a', 'SESSION_A');
    const view = render(<ChatSessionProvider><Probe /></ChatSessionProvider>);
    await waitFor(() => expect(screen.getByText('Tenant A chat|Tenant A chat')).toBeInTheDocument());

    mocks.listSessions.mockResolvedValue([]);
    await act(async () => {
      mocks.tenantId = 'tenant-b';
      view.rerender(<ChatSessionProvider><Probe /></ChatSessionProvider>);
    });

    await waitFor(() => expect(screen.getByText('|none')).toBeInTheDocument());
    expect(localStorage.getItem('chat_last_active_session_id')).toBeNull();
  });
});
