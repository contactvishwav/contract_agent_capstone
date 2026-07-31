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
import { ShieldCheck, AlertCircle } from 'lucide-react';
import { useAuth } from '../../../contexts/AuthContext';

const ROLES = [
  { value: 'ADMIN', label: 'Admin', description: 'Full access, including policy management and audit trail' },
  { value: 'LEGAL_REVIEWER', label: 'Legal Reviewer', description: 'Upload and analyze contracts, view reports' },
  { value: 'AUDITOR', label: 'Auditor', description: 'View reports and audit trail only' },
  { value: 'VIEWER', label: 'Viewer', description: 'Analyze/query only' },
];

export const LoginScreen: React.FC = () => {
  const { login, loginError, isLoggingIn } = useAuth();
  const [tenantId, setTenantId] = useState('');
  const [role, setRole] = useState('ADMIN');

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!tenantId.trim()) return;
    try {
      await login(tenantId.trim(), role);
    } catch {
      // loginError from context already reflects the failure
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
          <CardTitle className="text-xl">Sign in</CardTitle>
          <CardDescription>
            Issues a signed access token scoped to a tenant and role. There's no user/password
            system behind this yet - any tenant_id you enter here gets a real, verifiable token
            for that tenant, matching the same limitation documented on the token API itself.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            <div className="space-y-1.5">
              <label htmlFor="tenant-id" className="text-sm font-medium text-slate-700">
                Tenant ID
              </label>
              <input
                id="tenant-id"
                type="text"
                value={tenantId}
                onChange={(e) => setTenantId(e.target.value)}
                placeholder="e.g. acme-legal"
                autoFocus
                className="w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]"
              />
            </div>

            <div className="space-y-1.5">
              <label className="text-sm font-medium text-slate-700">Role</label>
              <Select value={role} onValueChange={setRole}>
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
                {ROLES.find((r) => r.value === role)?.description}
              </p>
            </div>

            {loginError && (
              <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                <span>{loginError}</span>
              </div>
            )}

            <Button type="submit" className="w-full" disabled={isLoggingIn || !tenantId.trim()}>
              {isLoggingIn ? 'Signing in...' : 'Sign in'}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  );
};
