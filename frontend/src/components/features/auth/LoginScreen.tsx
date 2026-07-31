import React, { useState } from 'react';
import { Button } from '../../shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../../shared/ui/card';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../../shared/ui/select';
import { ShieldCheck, AlertCircle, CheckCircle2 } from 'lucide-react';
import { useAuth } from '../../../contexts/AuthContext';

const ROLES = [
  { value: 'ADMIN', label: 'Admin', description: 'Full access, including policy management and audit trail' },
  { value: 'LEGAL_REVIEWER', label: 'Legal Reviewer', description: 'Upload and analyze contracts, view reports' },
  { value: 'AUDITOR', label: 'Auditor', description: 'View reports and audit trail only' },
  { value: 'VIEWER', label: 'Viewer', description: 'Analyze/query only' },
];

const inputClass =
  'w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]';

export const LoginScreen: React.FC = () => {
  const { login, loginError, isLoggingIn, register, registerError, isRegistering } = useAuth();
  const [mode, setMode] = useState<'login' | 'register'>('login');
  const [registeredNotice, setRegisteredNotice] = useState<string | null>(null);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');

  const [regUsername, setRegUsername] = useState('');
  const [regPassword, setRegPassword] = useState('');
  const [regTenantId, setRegTenantId] = useState('');
  const [regRole, setRegRole] = useState('ADMIN');

  const handleLogin = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    try {
      await login(username.trim(), password);
    } catch {
      // loginError from context already reflects the failure
    }
  };

  const handleRegister = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!regUsername.trim() || !regPassword || !regTenantId.trim()) return;
    try {
      await register({ username: regUsername.trim(), password: regPassword, tenantId: regTenantId.trim(), role: regRole });
      setUsername(regUsername.trim());
      setPassword('');
      setRegisteredNotice(`Account '${regUsername.trim()}' created - sign in below.`);
      setMode('login');
    } catch {
      // registerError from context already reflects the failure
    }
  };

  return (
    <div className="min-h-screen flex items-center justify-center bg-slate-50 px-4">
      <Card className="w-full max-w-md border-slate-200">
        <CardHeader>
          <div className="flex items-center gap-2 text-blue-600 mb-1">
            <ShieldCheck className="h-5 w-5" />
            <span className="text-xs font-semibold uppercase tracking-wide">Contract Intelligence</span>
          </div>
          <CardTitle className="text-xl">{mode === 'login' ? 'Sign in' : 'Create an account'}</CardTitle>
          <CardDescription>
            {mode === 'login'
              ? 'Sign in with a real account to receive a signed, tenant-scoped access token.'
              : "Minimal, self-service registration - there's no admin-provisioning system yet, so this is currently the only way to create an account."}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {mode === 'login' ? (
            <form onSubmit={handleLogin} className="space-y-4">
              {registeredNotice && (
                <div className="flex items-start gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
                  <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>{registeredNotice}</span>
                </div>
              )}

              <div className="space-y-1.5">
                <label htmlFor="username" className="text-sm font-medium text-slate-700">
                  Username
                </label>
                <input
                  id="username"
                  type="text"
                  value={username}
                  onChange={(e) => setUsername(e.target.value)}
                  autoFocus
                  autoComplete="username"
                  className={inputClass}
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="password" className="text-sm font-medium text-slate-700">
                  Password
                </label>
                <input
                  id="password"
                  type="password"
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  autoComplete="current-password"
                  className={inputClass}
                />
              </div>

              {loginError && (
                <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>{loginError}</span>
                </div>
              )}

              <Button type="submit" className="w-full" disabled={isLoggingIn || !username.trim() || !password}>
                {isLoggingIn ? 'Signing in...' : 'Sign in'}
              </Button>

              <button
                type="button"
                onClick={() => {
                  setMode('register');
                  setRegisteredNotice(null);
                }}
                className="w-full text-center text-sm text-slate-500 hover:text-slate-700"
              >
                Need an account? Create one
              </button>
            </form>
          ) : (
            <form onSubmit={handleRegister} className="space-y-4">
              <div className="space-y-1.5">
                <label htmlFor="reg-username" className="text-sm font-medium text-slate-700">
                  Username
                </label>
                <input
                  id="reg-username"
                  type="text"
                  value={regUsername}
                  onChange={(e) => setRegUsername(e.target.value)}
                  autoFocus
                  placeholder="3-64 characters: letters, numbers, . _ -"
                  className={inputClass}
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="reg-password" className="text-sm font-medium text-slate-700">
                  Password
                </label>
                <input
                  id="reg-password"
                  type="password"
                  value={regPassword}
                  onChange={(e) => setRegPassword(e.target.value)}
                  autoComplete="new-password"
                  placeholder="At least 8 characters"
                  className={inputClass}
                />
              </div>

              <div className="space-y-1.5">
                <label htmlFor="reg-tenant-id" className="text-sm font-medium text-slate-700">
                  Tenant ID
                </label>
                <input
                  id="reg-tenant-id"
                  type="text"
                  value={regTenantId}
                  onChange={(e) => setRegTenantId(e.target.value)}
                  placeholder="e.g. acme-legal"
                  className={inputClass}
                />
              </div>

              <div className="space-y-1.5">
                <label className="text-sm font-medium text-slate-700">Role</label>
                <Select value={regRole} onValueChange={setRegRole}>
                  <SelectTrigger className="w-full">
                    <SelectValue />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectGroup>
                      {ROLES.map((r) => (
                        <SelectItem key={r.value} value={r.value}>
                          {r.label}
                        </SelectItem>
                      ))}
                    </SelectGroup>
                  </SelectContent>
                </Select>
                <p className="text-xs text-slate-500">
                  {ROLES.find((r) => r.value === regRole)?.description}
                </p>
              </div>

              {registerError && (
                <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>{registerError}</span>
                </div>
              )}

              <Button
                type="submit"
                className="w-full"
                disabled={isRegistering || !regUsername.trim() || !regPassword || !regTenantId.trim()}
              >
                {isRegistering ? 'Creating account...' : 'Create account'}
              </Button>

              <button
                type="button"
                onClick={() => setMode('login')}
                className="w-full text-center text-sm text-slate-500 hover:text-slate-700"
              >
                Already have an account? Sign in
              </button>
            </form>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
