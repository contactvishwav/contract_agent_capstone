import { apiFetch } from '../lib/apiClient';

// Contract Chat image attachments (ADR-008). Same relative-path/apiFetch
// convention as chatSessionApi.ts. Client-side limits mirror the real
// server-enforced ones (backend/api/chat_sessions.py) exactly - not just
// trusting the server to reject, so the user gets immediate feedback
// instead of a round-trip failure.
export const MAX_ATTACHMENT_SIZE_BYTES = 5 * 1024 * 1024;
export const ALLOWED_ATTACHMENT_MIME_TYPES = ['image/png', 'image/jpeg', 'image/webp'];
export const MAX_ATTACHMENTS_PER_MESSAGE = 4;

export interface UploadedAttachment {
  attachment_id: string;
  mime_type: string;
  size_bytes: number;
}

// Distinct from a bare Error so the UI can always show the server's own
// real rejection reason (size/format/rate-limit) instead of a generic
// "upload failed" - no silent failures.
export class AttachmentUploadError extends Error {
  status: number;
  constructor(status: number, message: string) {
    super(message);
    this.name = 'AttachmentUploadError';
    this.status = status;
  }
}

async function extractErrorMessage(response: Response): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === 'string') return body.detail;
    if (body.detail && typeof body.detail.message === 'string') return body.detail.message;
    // slowapi's 429 handler uses {"error": "..."} , not {"detail": ...}.
    if (typeof body.error === 'string') return body.error;
  } catch {
    // Body wasn't JSON - fall through to the generic message below.
  }
  if (response.status === 429) return 'Too many uploads - please wait a moment and try again.';
  return response.statusText || 'Upload failed';
}

class ChatAttachmentApi {
  async upload(sessionId: string, file: File): Promise<UploadedAttachment> {
    const formData = new FormData();
    formData.append('file', file);
    let response: Response;
    try {
      response = await apiFetch(`/api/chat/sessions/${encodeURIComponent(sessionId)}/attachments`, {
        method: 'POST',
        body: formData,
      });
    } catch (error) {
      if (error instanceof Error && error.name === 'UnauthorizedError') throw error;
      throw new AttachmentUploadError(0, 'Network error - please check your connection and try again.');
    }
    if (!response.ok) {
      throw new AttachmentUploadError(response.status, await extractErrorMessage(response));
    }
    return response.json();
  }

  /** Authenticated bytes for an already-uploaded attachment, as an
   * in-memory object URL - same "never a public URL, always fetched with
   * the bearer token" posture as PdfCitationViewer.tsx's source fetch.
   * Caller owns the returned URL and must URL.revokeObjectURL it. */
  async fetchImageObjectUrl(sessionId: string, attachmentId: string): Promise<string> {
    const response = await apiFetch(
      `/api/chat/sessions/${encodeURIComponent(sessionId)}/attachments/${encodeURIComponent(attachmentId)}`,
      { cache: 'no-store' },
    );
    if (!response.ok) {
      throw new Error('Attachment unavailable');
    }
    const blob = await response.blob();
    return URL.createObjectURL(blob);
  }
}

export const chatAttachmentApi = new ChatAttachmentApi();

export function validateAttachmentFile(file: File): string | null {
  if (!ALLOWED_ATTACHMENT_MIME_TYPES.includes(file.type)) {
    return 'Only PNG, JPEG, or WEBP images are supported.';
  }
  if (file.size > MAX_ATTACHMENT_SIZE_BYTES) {
    return 'Image is too large (max 5MB).';
  }
  return null;
}
