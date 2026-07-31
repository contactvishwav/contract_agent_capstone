import React, { createContext, useContext, useSyncExternalStore, useCallback, useState } from 'react';
import { getSession, subscribe, setSessionFromToken, clearSession, AuthSession } from '../lib/authStore';

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
  login: (username: string, password: string) => Promise<void>;
  register: (fields: RegisterFields) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const session = useSyncExternalStore(subscribe, getSession, getSession);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);
  const [registerError, setRegisterError] = useState<string | null>(null);
  const [isRegistering, setIsRegistering] = useState(false);

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

      const { access_token } = await response.json();
      setSessionFromToken(access_token);
    } catch (err) {
      setLoginError(err instanceof Error ? err.message : 'Sign-in failed');
      throw err;
    } finally {
      setIsLoggingIn(false);
    }
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
        login,
        register,
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
