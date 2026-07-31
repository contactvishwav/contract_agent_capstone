// Plain (non-React) auth token store - the single source of truth for the
// current session's JWT. Lives outside React so both React components
// (via useAuth/AuthContext) and plain modules that can't use hooks
// (apiClient.ts's fetch wrapper, the EventSource-based chat call in
// input.tsx) can read/clear it the same way.
//
// Browser storage (localStorage) is appropriate here - this is the real
// deployed frontend, not a Claude artifact sandbox where storage APIs are
// unavailable/sandboxed.

const STORAGE_KEY = 'contract_intelligence_auth';

export interface AuthSession {
  token: string;
  tenantId: string;
  role: string;
  expiresAt: number; // ms since epoch, from the token's own `exp` claim
}

type Listener = () => void;

let session: AuthSession | null = loadFromStorage();
const listeners = new Set<Listener>();

function decodeJwtPayload(token: string): { tenant_id?: string; role?: string; exp?: number } | null {
  try {
    const payload = token.split('.')[1];
    // JWTs use base64url, not plain base64 - swap the two chars that differ.
    const base64 = payload.replace(/-/g, '+').replace(/_/g, '/');
    const json = decodeURIComponent(
      atob(base64)
        .split('')
        .map((c) => '%' + c.charCodeAt(0).toString(16).padStart(2, '0'))
        .join('')
    );
    return JSON.parse(json);
  } catch {
    return null;
  }
}

function loadFromStorage(): AuthSession | null {
  try {
    const raw = localStorage.getItem(STORAGE_KEY);
    if (!raw) return null;
    const parsed: AuthSession = JSON.parse(raw);
    if (!parsed.token || !parsed.expiresAt) return null;
    if (Date.now() >= parsed.expiresAt) {
      localStorage.removeItem(STORAGE_KEY);
      return null;
    }
    return parsed;
  } catch {
    return null;
  }
}

function notify() {
  listeners.forEach((listener) => listener());
}

export function getSession(): AuthSession | null {
  return session;
}

export function subscribe(listener: Listener): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

/** Stores a freshly-issued access token as the active session, derived
 * entirely from its own claims (not from whatever the login form
 * submitted) so the UI always reflects what the token actually grants. */
export function setSessionFromToken(token: string): AuthSession {
  const claims = decodeJwtPayload(token);
  if (!claims?.tenant_id || !claims?.role || !claims?.exp) {
    throw new Error('Received an unusable token from the server (missing claims)');
  }
  const next: AuthSession = {
    token,
    tenantId: claims.tenant_id,
    role: claims.role,
    expiresAt: claims.exp * 1000,
  };
  session = next;
  localStorage.setItem(STORAGE_KEY, JSON.stringify(next));
  notify();
  return next;
}

export function clearSession() {
  session = null;
  localStorage.removeItem(STORAGE_KEY);
  notify();
}

/** `Authorization` header value for the active session, or null if
 * there isn't one - used by both apiClient.ts's fetch wrapper and the
 * fetchEventSource-based chat call, which can't share a fetch wrapper. */
export function authHeader(): string | null {
  return session ? `Bearer ${session.token}` : null;
}
