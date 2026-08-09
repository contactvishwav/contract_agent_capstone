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
  requested_model?: string | null;
  actual_model?: string | null;
  requested_provider?: string | null;
  actual_provider?: string | null;
  fallback_occurred?: boolean;
  fallback_reason?: string | null;
  prompt_version?: string | null;
  execution_path?: string | null;
  tool_name: string | null;
  tool_call_id: string | null;
  citations: ChatCitation[];
  terminal_status?: 'passed' | 'rejected' | 'validation_failed' | 'timed_out' | 'cancelled' | 'empty' | 'generation_failed' | 'persistence_failed' | null;
  sequence: number;
  created_at: string | null;
}

export interface ChatCitation {
  citation_id: string;
  contract_id: string;
  filename: string;
  source_type: 'document' | 'section' | 'clause' | 'relationship' | 'chunk';
  page: number | null;
  section_id: string | null;
  section_title: string | null;
  clause_id: string | null;
  clause_type: string | null;
  chunk_id: string | null;
  chunk_index: number | null;
  start_offset: number | null;
  end_offset: number | null;
  excerpt: string | null;
  tool_name: string | null;
  tool_call_id: string | null;
  validation_status: 'tenant_active';
  highlight_text?: string | null;
  page_start_offset?: number | null;
  page_end_offset?: number | null;
  source_available?: boolean;
  provenance_status?: 'exact' | 'page_only' | 'source_excerpt_only' | 'legacy_excerpt' | 'unsupported_image_only';
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

  async renameSession(sessionId: string, title: string): Promise<ChatSessionSummary> {
    const response = await apiFetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}`, {
      method: 'PATCH',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ title }),
    });
    if (!response.ok) {
      throw new Error(`Failed to rename chat session: ${response.statusText}`);
    }
    return response.json();
  }
}

export const chatSessionApi = new ChatSessionApi();
