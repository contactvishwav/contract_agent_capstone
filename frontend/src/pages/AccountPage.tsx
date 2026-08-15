import React, { useState } from 'react';
import { QRCodeSVG } from 'qrcode.react';
import { Button } from '../components/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/shared/ui/card';
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '../components/shared/ui/select';
import { AlertCircle, CheckCircle2, ShieldCheck, UserPlus } from 'lucide-react';
import { authApi, InviteCreateResponse } from '../services/authApi';
import { useAuth } from '../contexts/AuthContext';
import { ROLES } from '../lib/roles';
import { apiFetch } from '../lib/apiClient';

const inputClass =
  'w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]';

type MfaSetupState = 'idle' | 'confirming' | 'enabled';

/**
 * Real MFA setup UI - backend/api/auth_api.py's POST /mfa/setup, /confirm
 * have always supported this, but nothing in the app let a real user turn
 * it on (found in the credential-provisioning audit). QR rendering is
 * entirely client-side (qrcode.react, no network call, no external image
 * request) - the provisioning URI itself never leaves the browser except
 * to whatever authenticator app the user scans it with.
 */
export const AccountPage: React.FC = () => {
  const [setupState, setSetupState] = useState<MfaSetupState>('idle');
  const [provisioningUri, setProvisioningUri] = useState<string | null>(null);
  const [confirmCode, setConfirmCode] = useState('');
  const [backupCodes, setBackupCodes] = useState<string[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  const startSetup = async () => {
    setError(null);
    setIsSubmitting(true);
    try {
      const { provisioning_uri } = await authApi.setupMfa();
      setProvisioningUri(provisioning_uri);
      setSetupState('confirming');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not start MFA setup');
    } finally {
      setIsSubmitting(false);
    }
  };

  const confirmSetup = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!confirmCode.trim()) return;
    setError(null);
    setIsSubmitting(true);
    try {
      const { backup_codes } = await authApi.confirmMfa(confirmCode.trim());
      setBackupCodes(backup_codes);
      setSetupState('enabled');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Invalid or expired code');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <div className="max-w-2xl space-y-6">
      <Card className="border-slate-200">
        <CardHeader>
          <div className="flex items-center gap-2 text-blue-600 mb-1">
            <ShieldCheck className="h-5 w-5" />
            <span className="text-xs font-semibold uppercase tracking-wide">Security</span>
          </div>
          <CardTitle className="text-xl">Two-factor authentication</CardTitle>
          <CardDescription>
            {setupState === 'enabled'
              ? 'Enabled. Every future sign-in will ask for a code from your authenticator app.'
              : 'Add a second factor (TOTP - Google Authenticator, Authy, 1Password, etc.) required on every future sign-in, plus 10 single-use backup codes for if you lose access to it.'}
          </CardDescription>
        </CardHeader>
        <CardContent>
          {setupState === 'idle' && (
            <>
              {error && (
                <div className="mb-4 flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                  <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                  <span>{error}</span>
                </div>
              )}
              <Button onClick={startSetup} disabled={isSubmitting}>
                {isSubmitting ? 'Starting...' : 'Enable two-factor authentication'}
              </Button>
            </>
          )}

          {setupState === 'confirming' && provisioningUri && (
            <div className="space-y-4">
              <div className="flex flex-col items-center gap-3 rounded-md border border-slate-200 bg-white p-4">
                <QRCodeSVG value={provisioningUri} size={192} />
                <p className="text-xs text-slate-500 text-center break-all">
                  Can't scan? Enter this manually in your authenticator app:
                  <br />
                  <code className="text-slate-700">{provisioningUri}</code>
                </p>
              </div>

              <form onSubmit={confirmSetup} className="space-y-3">
                <div className="space-y-1.5">
                  <label htmlFor="confirm-code" className="text-sm font-medium text-slate-700">
                    Enter the 6-digit code from your app to confirm
                  </label>
                  <input
                    id="confirm-code"
                    type="text"
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    value={confirmCode}
                    onChange={(e) => setConfirmCode(e.target.value)}
                    autoFocus
                    className={inputClass}
                  />
                </div>

                {error && (
                  <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                    <span>{error}</span>
                  </div>
                )}

                <Button type="submit" disabled={isSubmitting || !confirmCode.trim()}>
                  {isSubmitting ? 'Confirming...' : 'Confirm and enable'}
                </Button>
              </form>
            </div>
          )}

          {setupState === 'enabled' && backupCodes && (
            <div className="space-y-4">
              <div className="flex items-start gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
                <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
                <span>Two-factor authentication is now enabled.</span>
              </div>

              <div className="rounded-md border border-amber-200 bg-amber-50 p-4">
                <p className="text-sm font-medium text-amber-800 mb-2">
                  Save these backup codes now - they will not be shown again.
                </p>
                <p className="text-xs text-amber-700 mb-3">
                  Each one can be used once, in place of a 6-digit code, if you lose access to your authenticator app.
                </p>
                <ul className="grid grid-cols-2 gap-2 font-mono text-sm">
                  {backupCodes.map((code) => (
                    <li key={code} className="rounded bg-white border border-amber-200 px-2 py-1 text-center">
                      {code}
                    </li>
                  ))}
                </ul>
              </div>
            </div>
          )}
        </CardContent>
      </Card>

      <InviteSection />
      <PendingReviewsSection />
    </div>
  );
};

/**
 * Minimal admin invite UI - backend/api/auth_api.py's POST /api/auth/
 * invites has always existed, but the only way to use it was a raw curl
 * call (found in the credential-provisioning audit). ADMIN-only, same
 * boundary the backend itself enforces (Permission.MANAGE_USERS) - this
 * component hiding for non-admins is a UX nicety, not the real
 * authorization (the backend would reject the call regardless).
 *
 * No invite-listing endpoint exists yet, so this shows the result of the
 * invite just created rather than a historical list - real and usable
 * for the one thing the backend actually supports today, not a UI ahead
 * of what's real.
 */
const InviteSection: React.FC = () => {
  const { session } = useAuth();
  // Hooks must run unconditionally (Rules of Hooks) - the ADMIN-only gate
  // is applied after all of them, not before, so this component doesn't
  // break if `session.role` ever changes without a remount.
  const [email, setEmail] = useState('');
  const [role, setRole] = useState('VIEWER');
  const [result, setResult] = useState<InviteCreateResponse | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [isSubmitting, setIsSubmitting] = useState(false);

  if (session?.role !== 'ADMIN') return null;

  const handleInvite = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!email.trim()) return;
    setError(null);
    setResult(null);
    setIsSubmitting(true);
    try {
      const response = await authApi.createInvite(email.trim(), role);
      setResult(response);
      setEmail('');
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not create invite');
    } finally {
      setIsSubmitting(false);
    }
  };

  return (
    <Card className="border-slate-200">
      <CardHeader>
        <div className="flex items-center gap-2 text-blue-600 mb-1">
          <UserPlus className="h-5 w-5" />
          <span className="text-xs font-semibold uppercase tracking-wide">Organization</span>
        </div>
        <CardTitle className="text-xl">Invite a teammate</CardTitle>
        <CardDescription>
          Invite someone to join <span className="font-medium text-slate-700">{session.tenantId}</span>. They'll get a real
          email with a link to set up their own account.
        </CardDescription>
      </CardHeader>
      <CardContent>
        <form onSubmit={handleInvite} className="space-y-4">
          <div className="space-y-1.5">
            <label htmlFor="invite-email" className="text-sm font-medium text-slate-700">
              Email
            </label>
            <input
              id="invite-email"
              type="email"
              value={email}
              onChange={(e) => setEmail(e.target.value)}
              placeholder="teammate@example.com"
              className={inputClass}
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
            <p className="text-xs text-slate-500">{ROLES.find((r) => r.value === role)?.description}</p>
          </div>

          {error && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>{error}</span>
            </div>
          )}

          {result && (
            <div className="flex items-start gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
              <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
              <span>
                Invite created for {result.email} ({result.role}).{' '}
                {result.email_sent
                  ? 'Email sent.'
                  : "Email could not be sent (check the Resend sender configuration) - share the accept link with them another way."}
              </span>
            </div>
          )}

          <Button type="submit" disabled={isSubmitting || !email.trim()}>
            {isSubmitting ? 'Sending...' : 'Send invite'}
          </Button>
        </form>
      </CardContent>
    </Card>
  );
};

interface PendingReview {
  contract_id: string;
  filename: string | null;
  risk_score: number | null;
  risk_level: string | null;
  requested_at: string | null;
}

/**
 * Phase 4 (HITL) admin review queue: contracts paused at
 * human_review_gate (backend/agents/contract_intelligence_agents.py) for
 * this tenant, with Approve/Reject actions hitting the new
 * .../review/approve|reject endpoints. Same ADMIN-only UX-nicety gating
 * as InviteSection above - the real authorization is requires_role
 * (ADMIN) on the backend routes themselves.
 */
const PendingReviewsSection: React.FC = () => {
  const { session } = useAuth();
  const [reviews, setReviews] = useState<PendingReview[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [actioningId, setActioningId] = useState<string | null>(null);

  const loadReviews = React.useCallback(async () => {
    setLoading(true);
    setError(null);
    try {
      const response = await apiFetch('/api/intelligence/contracts/reviews/pending');
      if (!response.ok) throw new Error(`Failed to load pending reviews: ${response.statusText}`);
      const body = await response.json();
      setReviews(body.reviews || []);
    } catch (err) {
      setError(err instanceof Error ? err.message : 'Could not load pending reviews');
    } finally {
      setLoading(false);
    }
  }, []);

  React.useEffect(() => {
    if (session?.role === 'ADMIN') void loadReviews();
  }, [session?.role, loadReviews]);

  if (session?.role !== 'ADMIN') return null;

  const handleAction = async (contractId: string, action: 'approve' | 'reject') => {
    setActioningId(contractId);
    setError(null);
    try {
      const response = await apiFetch(`/api/intelligence/contracts/${encodeURIComponent(contractId)}/review/${action}`, {
        method: 'POST',
      });
      if (!response.ok) throw new Error(`Failed to ${action} review: ${response.statusText}`);
      await loadReviews();
    } catch (err) {
      setError(err instanceof Error ? err.message : `Could not ${action} review`);
    } finally {
      setActioningId(null);
    }
  };

  return (
    <Card className="border-slate-200">
      <CardHeader>
        <div className="flex items-center gap-2 text-amber-600 mb-1">
          <AlertCircle className="h-5 w-5" />
          <span className="text-xs font-semibold uppercase tracking-wide">Human Review Queue</span>
        </div>
        <CardTitle className="text-xl">Pending contract reviews</CardTitle>
        <CardDescription>
          Contracts whose risk assessment came back HIGH or CRITICAL pause here for approval before
          redline suggestions are generated.
        </CardDescription>
      </CardHeader>
      <CardContent className="space-y-3">
        {error && (
          <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
            <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
            <span>{error}</span>
          </div>
        )}

        {loading && reviews.length === 0 && (
          <p className="text-sm text-slate-500">Loading pending reviews…</p>
        )}

        {!loading && reviews.length === 0 && !error && (
          <p className="text-sm text-slate-500">No contracts are currently pending review.</p>
        )}

        {reviews.map((review) => (
          <div
            key={review.contract_id}
            data-testid="pending-review-row"
            className="flex items-center justify-between rounded-md border border-amber-200 bg-amber-50 px-3 py-2"
          >
            <div className="min-w-0">
              <p className="truncate text-sm font-medium text-slate-800">{review.filename || review.contract_id}</p>
              <p className="text-xs text-amber-700">
                {review.risk_level || 'HIGH/CRITICAL'}
                {typeof review.risk_score === 'number' ? ` · ${review.risk_score}/100` : ''}
              </p>
            </div>
            <div className="flex items-center gap-2">
              <Button
                size="sm"
                variant="outline"
                disabled={actioningId === review.contract_id}
                onClick={() => handleAction(review.contract_id, 'reject')}
              >
                Reject
              </Button>
              <Button
                size="sm"
                disabled={actioningId === review.contract_id}
                onClick={() => handleAction(review.contract_id, 'approve')}
              >
                {actioningId === review.contract_id ? 'Working…' : 'Approve'}
              </Button>
            </div>
          </div>
        ))}
      </CardContent>
    </Card>
  );
};
