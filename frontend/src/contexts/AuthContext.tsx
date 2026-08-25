import React, { createContext, useContext, useSyncExternalStore, useCallback, useState } from 'react';
import { getSession, subscribe, setSessionFromToken, clearSession, AuthSession } from '../lib/authStore';
import { authApi } from '../services/authApi';

interface RegisterFields {
  username: string;
  password: string;
  tenantId: string;
  role: string;
}

interface AuthContextType {
  session: AuthSession | null;
  isAuthenticated: boolean;
  loginError: string | null;
  isLoggingIn: boolean;
  registerError: string | null;
  isRegistering: boolean;
  // MFA (docs/CAPSTONE_SUMMARY.md - credential provisioning): POST
  // /api/auth/token returns {mfa_required: true, mfa_token} instead of a
  // token when the matched account has MFA enabled - mfaRequired reflects
  // that second login step is pending, not yet complete.
  mfaRequired: boolean;
  mfaError: string | null;
  isVerifyingMfa: boolean;
  login: (username: string, password: string) => Promise<void>;
  register: (fields: RegisterFields) => Promise<void>;
  verifyMfaCode: (code: string) => Promise<void>;
  cancelMfa: () => void;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const session = useSyncExternalStore(subscribe, getSession, getSession);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);
  const [pendingMfaToken, setPendingMfaToken] = useState<string | null>(null);
  const [mfaError, setMfaError] = useState<string | null>(null);
  const [isVerifyingMfa, setIsVerifyingMfa] = useState(false);

  const login = useCallback(async (username: string, password: string) => {
    setIsLoggingIn(true);
    setLoginError(null);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      // Deliberately a plain fetch, not apiFetch: issuing a token is the
      // one call that must work with no session yet, and must never be
      // treated as "the existing session got a 401".
      const response = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Sign-in failed (${response.status})`);
      }

      const body = await response.json();
      if (body.mfa_required) {
        setPendingMfaToken(body.mfa_token);
        setMfaError(null);
        return;
      }
      setSessionFromToken(body.access_token);
    } catch (err: any) {
      clearTimeout(timeoutId);
      let msg = 'Authentication server unavailable. Please try again.';
      if (err.name === 'AbortError') {
        msg = 'Authentication server unavailable. Please try again.';
      } else if (err instanceof Error && err.message && !err.message.includes('Failed to fetch')) {
        msg = err.message;
      }
      setLoginError(msg);
      throw new Error(msg);
    } finally {
      clearTimeout(timeoutId);
      setIsLoggingIn(false);
    }
  }, []);

  const verifyMfaCode = useCallback(async (code: string) => {
    if (!pendingMfaToken) return;
    setIsVerifyingMfa(true);
    setMfaError(null);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      const result = await authApi.verifyMfa(pendingMfaToken, code);
      clearTimeout(timeoutId);
      if (!result.access_token) {
        throw new Error('Verification succeeded but the server did not return a usable token');
      }
      setSessionFromToken(result.access_token);
      setPendingMfaToken(null);
    } catch (err: any) {
      clearTimeout(timeoutId);
      let msg = 'Authentication server unavailable. Please try again.';
      if (err.name === 'AbortError') {
        msg = 'Authentication server unavailable. Please try again.';
      } else if (err instanceof Error && err.message && !err.message.includes('Failed to fetch')) {
        msg = err.message;
      }
      setMfaError(msg);
      throw new Error(msg);
    } finally {
      clearTimeout(timeoutId);
      setIsVerifyingMfa(false);
    }
  }, [pendingMfaToken]);

  const cancelMfa = useCallback(() => {
    setPendingMfaToken(null);
    setMfaError(null);
  }, []);

  const register = useCallback(async ({ username, password, tenantId, role }: RegisterFields) => {
    setIsRegistering(true);
    setRegisterError(null);
    const controller = new AbortController();
    const timeoutId = setTimeout(() => controller.abort(), 10000);

    try {
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, tenant_id: tenantId, role }),
        signal: controller.signal,
      });

      clearTimeout(timeoutId);

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Registration failed (${response.status})`);
      }
    } catch (err: any) {
      clearTimeout(timeoutId);
      let msg = 'Authentication server unavailable. Please try again.';
      if (err.name === 'AbortError') {
        msg = 'Authentication server unavailable. Please try again.';
      } else if (err instanceof Error && err.message && !err.message.includes('Failed to fetch')) {
        msg = err.message;
      }
      setRegisterError(msg);
      throw new Error(msg);
    } finally {
      clearTimeout(timeoutId);
      setIsRegistering(false);
    }
  }, []);

  const logout = useCallback(() => {
    clearSession();
  }, []);

  return (
    <AuthContext.Provider
      value={{
        session,
        isAuthenticated: session !== null,
        loginError,
        isLoggingIn,
        registerError,
        isRegistering,
        mfaRequired: pendingMfaToken !== null,
        mfaError,
        isVerifyingMfa,
        login,
        register,
        verifyMfaCode,
        cancelMfa,
        logout,
      }}
    >
      {children}
    </AuthContext.Provider>
  );
};

export const useAuth = () => {
  const context = useContext(AuthContext);
  if (!context) {
    throw new Error('useAuth must be used within an AuthProvider');
  }
  return context;
};
