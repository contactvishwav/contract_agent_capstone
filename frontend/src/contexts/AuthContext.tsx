import React, { createContext, useContext, useSyncExternalStore, useCallback, useState } from 'react';
import { getSession, subscribe, setSessionFromToken, clearSession, AuthSession } from '../lib/authStore';

interface AuthContextType {
  session: AuthSession | null;
  isAuthenticated: boolean;
  loginError: string | null;
  isLoggingIn: boolean;
  login: (tenantId: string, role: string) => Promise<void>;
  logout: () => void;
}

const AuthContext = createContext<AuthContextType | undefined>(undefined);

export const AuthProvider: React.FC<{ children: React.ReactNode }> = ({ children }) => {
  const session = useSyncExternalStore(subscribe, getSession, getSession);
  const [loginError, setLoginError] = useState<string | null>(null);
  const [isLoggingIn, setIsLoggingIn] = useState(false);

  const login = useCallback(async (tenantId: string, role: string) => {
    setIsLoggingIn(true);
    setLoginError(null);
    try {
      // Deliberately a plain fetch, not apiFetch: issuing a token is the
      // one call that must work with no session yet, and must never be
      // treated as "the existing session got a 401".
      const response = await fetch('/api/auth/token', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ tenant_id: tenantId, role }),
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
        login,
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
