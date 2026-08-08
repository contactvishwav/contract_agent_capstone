import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { chatSessionApi, ChatSessionSummary } from '../services/chatSessionApi';
import { useAuth } from './AuthContext';

interface ChatSessionContextType {
  sessions: ChatSessionSummary[];
  // The session currently open in the Chat UI. null means "not yet
  // created" (see createSession below - session creation is lazy, so a
  // "New chat" click doesn't immediately POST) as well as the initial
  // blank-slate state before anything has ever been selected.
  activeSession: ChatSessionSummary | null;
  isLoadingSessions: boolean;
  sessionListError: string | null;
  refreshSessions: () => Promise<void>;
  // Creates a real session server-side and makes it active. Called lazily
  // by ChatInput on first send, not eagerly on "New chat" - an unused
  // "New chat" click must never pollute the switcher with an empty thread.
  createSession: (contractId: string | null, title?: string) => Promise<ChatSessionSummary>;
  selectSession: (session: ChatSessionSummary) => void;
  // Clears the active session back to "not yet created" - the next send
  // will lazily create a fresh one.
  startNewSession: () => void;
}

const ChatSessionContext = createContext<ChatSessionContextType | undefined>(undefined);

// Convenience-only, not the source of truth: which session was last open,
// so a page refresh reopens the same conversation instead of landing on a
// blank slate - the actual conversation content always comes from the
// backend (GET /api/chat/sessions/{id}), never from localStorage.
const LAST_ACTIVE_SESSION_KEY = 'chat_last_active_session_id';

export const ChatSessionProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const { session } = useAuth();
  const tenantId = session?.tenantId ?? null;
  const lastActiveKey = tenantId ? `${LAST_ACTIVE_SESSION_KEY}:${tenantId}` : null;
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSessionSummary | null>(null);
  const [isLoadingSessions, setIsLoadingSessions] = useState(false);
  const [sessionListError, setSessionListError] = useState<string | null>(null);

  const refreshSessions = useCallback(async () => {
    if (!tenantId) return;
    setIsLoadingSessions(true);
    setSessionListError(null);
    try {
      const rows = await chatSessionApi.listSessions();
      setSessions(rows);
    } catch {
      setSessionListError('Could not load conversations.');
    } finally {
      setIsLoadingSessions(false);
    }
  }, [tenantId]);

  useEffect(() => {
    setSessions([]);
    setActiveSession(null);
    refreshSessions();
  }, [tenantId, refreshSessions]);

  // Restore the last-open session (if any) once the real list has loaded,
  // so a refresh reopens the same conversation rather than landing on a
  // blank slate - only ever a convenience default, never trusted as the
  // conversation's actual content.
  useEffect(() => {
    if (activeSession || sessions.length === 0) {
      return;
    }
    if (!lastActiveKey) return;
    const lastId = localStorage.getItem(lastActiveKey);
    if (!lastId) {
      return;
    }
    const match = sessions.find((s) => s.session_id === lastId);
    if (match) {
      setActiveSession(match);
    }
  }, [activeSession, lastActiveKey, sessions]);

  const createSession = useCallback(async (contractId: string | null, title?: string) => {
    const created = await chatSessionApi.createSession(contractId, title);
    setSessions((prev) => [created, ...prev]);
    setActiveSession(created);
    if (lastActiveKey) localStorage.setItem(lastActiveKey, created.session_id);
    return created;
  }, [lastActiveKey]);

  const selectSession = useCallback((session: ChatSessionSummary) => {
    setActiveSession(session);
    if (lastActiveKey) localStorage.setItem(lastActiveKey, session.session_id);
  }, [lastActiveKey]);

  const startNewSession = useCallback(() => {
    setActiveSession(null);
    if (lastActiveKey) localStorage.removeItem(lastActiveKey);
  }, [lastActiveKey]);

  return (
    <ChatSessionContext.Provider
      value={{
        sessions,
        activeSession,
        isLoadingSessions,
        sessionListError,
        refreshSessions,
        createSession,
        selectSession,
        startNewSession,
      }}
    >
      {children}
    </ChatSessionContext.Provider>
  );
};

export const useChatSession = () => {
  const context = useContext(ChatSessionContext);
  if (!context) {
    throw new Error('useChatSession must be used within ChatSessionProvider');
  }
  return context;
};
