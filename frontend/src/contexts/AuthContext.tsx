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
    try {
      // Deliberately a plain fetch, not apiFetch: issuing a token is the
      // one call that must work with no session yet, and must never be
      // treated as "the existing session got a 401".
      const response = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Sign-in failed (${response.status})`);
      }

      const body = await response.json();
      if (body.mfa_required) {
        // No access_token in this response at all (real credentials were
        // still verified - see api/auth_api.py's issue_token) - the login
        // isn't complete until verifyMfaCode succeeds, so no session is
        // established yet. Previously this branch didn't exist: the code
        // unconditionally destructured access_token (undefined here) and
        // handed it to setSessionFromToken, which threw a generic
        // "unusable token" error - any MFA-enabled account was completely
        // locked out of the web app. Found in the credential-provisioning
        // audit, fixed here.
        setPendingMfaToken(body.mfa_token);
        setMfaError(null);
        return;
      }
      setSessionFromToken(body.access_token);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Sign-in failed');
      throw err;
    } finally {
      setIsLoggingIn(false);
    }
  }, []);

  const verifyMfaCode = useCallback(async (code: string) => {
    if (!pendingMfaToken) return;
    setIsVerifyingMfa(true);
    setMfaError(null);
    try {
      const result = await authApi.verifyMfa(pendingMfaToken, code);
      if (!result.access_token) {
        throw new Error('Verification succeeded but the server did not return a usable token');
      }
      setSessionFromToken(result.access_token);
      setPendingMfaToken(null);
    } catch (err) {
      setMfaError(err instanceof Error ? err.message : 'Verification failed');
      throw err;
    } finally {
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
    try {
      const response = await fetch('/api/auth/register', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ username, password, tenant_id: tenantId, role }),
      });

      if (!response.ok) {
        const body = await response.json().catch(() => ({}));
        throw new Error(body.detail || `Registration failed (${response.status})`);
      }
    } catch (err) {
      setRegisterError(err instanceof Error ? err.message : 'Registration failed');
      throw err;
    } finally {
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
