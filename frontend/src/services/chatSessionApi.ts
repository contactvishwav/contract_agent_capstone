import { apiFetch } from '../lib/apiClient';

// Persistent Contract Chat sessions (backend/api/chat_sessions.py). Relative
// paths against the current origin, same reasoning as enhancedSearchApi.ts -
// nginx's /api/ proxy and the Vite dev proxy both expect this, not an
// absolute localhost URL.

export interface ChatSessionSummary {
  session_id: string;
  contract_id: string | null;
  title: string;
  created_at: string | null;
  updated_at: string | null;
  message_count: number;
}

export interface ChatSessionMessage {
  message_id: string;
  role: 'user_message' | 'ai_message' | 'tool_call' | 'tool_message';
  content: string;
  model: string | null;
  tool_name: string | null;
  sequence: number;
  created_at: string | null;
}

export interface ChatSessionDetail extends ChatSessionSummary {
  messages: ChatSessionMessage[];
}

class ChatSessionApi {
  async listSessions(contractId?: string | null): Promise<ChatSessionSummary[]> {
    const query = contractId ? `?contract_id=${encodeURIComponent(contractId)}` : '';
    const response = await apiFetch(`/api/chat/sessions${query}`);
    if (!response.ok) {
      throw new Error(`Failed to list chat sessions: ${response.statusText}`);
    }
    return response.json();
  }

  async createSession(contractId: string | null, title?: string): Promise<ChatSessionSummary> {
    const response = await apiFetch('/api/chat/sessions', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ contract_id: contractId, title: title || null }),
    });
    if (!response.ok) {
      throw new Error(`Failed to create chat session: ${response.statusText}`);
    }
    return response.json();
  }

  async getSessionDetail(sessionId: string): Promise<ChatSessionDetail> {
    const response = await apiFetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`);
    if (!response.ok) {
      throw new Error(`Failed to load chat session: ${response.statusText}`);
    }
    return response.json();
  }
}

export const chatSessionApi = new ChatSessionApi();
