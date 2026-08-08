import React, { createContext, useContext, useState, useEffect, useCallback } from 'react';
import { chatSessionApi, ChatSessionSummary } from '../services/chatSessionApi';

interface ChatSessionContextType {
  sessions: ChatSessionSummary[];
  // The session currently open in the Chat UI. null means "not yet
  // created" (see createSession below - session creation is lazy, so a
  // "New chat" click doesn't immediately POST) as well as the initial
  // blank-slate state before anything has ever been selected.
  activeSession: ChatSessionSummary | null;
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
  const [sessions, setSessions] = useState<ChatSessionSummary[]>([]);
  const [activeSession, setActiveSession] = useState<ChatSessionSummary | null>(null);

  const refreshSessions = useCallback(async () => {
    try {
      const rows = await chatSessionApi.listSessions();
      setSessions(rows);
    } catch (e) {
      // Chat still works without a switcher populated - a listing failure
      // (e.g. transient network issue) shouldn't block sending messages.
    }
  }, []);

  useEffect(() => {
    refreshSessions();
  }, [refreshSessions]);

  // Restore the last-open session (if any) once the real list has loaded,
  // so a refresh reopens the same conversation rather than landing on a
  // blank slate - only ever a convenience default, never trusted as the
  // conversation's actual content.
  useEffect(() => {
    if (activeSession || sessions.length === 0) {
      return;
    }
    const lastId = localStorage.getItem(LAST_ACTIVE_SESSION_KEY);
    if (!lastId) {
      return;
    }
    const match = sessions.find((s) => s.session_id === lastId);
    if (match) {
      setActiveSession(match);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [sessions]);

  const createSession = useCallback(async (contractId: string | null, title?: string) => {
    const created = await chatSessionApi.createSession(contractId, title);
    setSessions((prev) => [created, ...prev]);
    setActiveSession(created);
    localStorage.setItem(LAST_ACTIVE_SESSION_KEY, created.session_id);
    return created;
  }, []);

  const selectSession = useCallback((session: ChatSessionSummary) => {
    setActiveSession(session);
    localStorage.setItem(LAST_ACTIVE_SESSION_KEY, session.session_id);
  }, []);

  const startNewSession = useCallback(() => {
    setActiveSession(null);
    localStorage.removeItem(LAST_ACTIVE_SESSION_KEY);
  }, []);

  return (
    <ChatSessionContext.Provider
      value={{ sessions, activeSession, refreshSessions, createSession, selectSession, startNewSession }}
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
