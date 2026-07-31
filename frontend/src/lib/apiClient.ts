// Shared fetch wrapper: every authenticated call in the app should go
// through apiFetch instead of calling fetch() directly, so the
// Authorization header and 401 handling live in exactly one place rather
// than being copy-pasted into every component.
import { getSession, clearSession, authHeader } from './authStore';

export class UnauthorizedError extends Error {
  constructor() {
    super('Session expired or invalid - please sign in again');
    this.name = 'UnauthorizedError';
  }
}

/** fetch(), but with the current session's bearer token attached and a
 * 401 response treated as "log the user out" rather than left for every
 * caller to notice (or not) individually. A 401 here always means the
 * token itself was rejected server-side (missing, expired, tampered) -
 * not a permissions problem, which the backend reports as 403 instead. */
export async function apiFetch(input: RequestInfo | URL, init: RequestInit = {}): Promise<Response> {
  const headers = new Headers(init.headers);
  const auth = authHeader();
  if (auth) {
    headers.set('Authorization', auth);
  }

  const response = await fetch(input, { ...init, headers });

  if (response.status === 401 && getSession()) {
    // Only treat this as a session failure if we thought we were logged
    // in - avoids clearing an already-empty session in a loop.
    clearSession();
    throw new UnauthorizedError();
  }

  return response;
}
