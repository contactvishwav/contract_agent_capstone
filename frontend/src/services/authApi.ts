// Typed client for the credential-provisioning endpoints (MFA, org
// invites) - backend/api/auth_api.py. Same relative-path convention as
// every other service module in this app (enhancedSearchApi.ts) - no
// hardcoded origin, works through both the Vite dev proxy and nginx's
// /api/ reverse proxy.
import { apiFetch } from '../lib/apiClient';

export interface MfaSetupResponse {
  provisioning_uri: string;
}

export interface MfaConfirmResponse {
  backup_codes: string[];
}

export interface MfaVerifyResponse {
  access_token: string | null;
  token_type: string;
  expires_in: number | null;
}

export interface InviteCreateResponse {
  email: string;
  tenant_id: string;
  role: string;
  email_sent: boolean;
}

export interface InvitePreviewResponse {
  email: string;
  tenant_id: string;
  role: string;
}

async function parseErrorDetail(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => ({}));
  return body.detail || `${fallback} (${response.status})`;
}

class AuthApi {
  async setupMfa(): Promise<MfaSetupResponse> {
    const response = await apiFetch('/api/auth/mfa/setup', { method: 'POST' });
    if (!response.ok) throw new Error(await parseErrorDetail(response, 'MFA setup failed'));
    return response.json();
  }

  async confirmMfa(code: string): Promise<MfaConfirmResponse> {
    const response = await apiFetch('/api/auth/mfa/confirm', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ code }),
    });
    if (!response.ok) throw new Error(await parseErrorDetail(response, 'Confirmation failed'));
    return response.json();
  }

  /** Deliberately a plain fetch, not apiFetch - there is no session yet
   * at this point in the login flow, same rationale as AuthContext's
   * own POST /api/auth/token call. */
  async verifyMfa(mfaToken: string, code: string): Promise<MfaVerifyResponse> {
    const response = await fetch('/api/auth/mfa/verify', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ mfa_token: mfaToken, code }),
    });
    if (!response.ok) throw new Error(await parseErrorDetail(response, 'Verification failed'));
    return response.json();
  }

  async createInvite(email: string, role: string): Promise<InviteCreateResponse> {
    const response = await apiFetch('/api/auth/invites', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ email, role }),
    });
    if (!response.ok) throw new Error(await parseErrorDetail(response, 'Invite creation failed'));
    return response.json();
  }

  /** Public - no session required, used by the invite-accept page a new
   * teammate lands on before they have any account at all. */
  async previewInvite(token: string): Promise<InvitePreviewResponse> {
    const response = await fetch(`/api/auth/invites/${encodeURIComponent(token)}`);
    if (!response.ok) throw new Error(await parseErrorDetail(response, 'Invite not found'));
    return response.json();
  }

  async acceptInvite(token: string, username: string, password: string): Promise<{ username: string; tenant_id: string; role: string }> {
    const response = await fetch(`/api/auth/invites/${encodeURIComponent(token)}/accept`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password }),
    });
    if (!response.ok) throw new Error(await parseErrorDetail(response, 'Could not accept invite'));
    return response.json();
  }
}

export const authApi = new AuthApi();
