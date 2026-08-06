import React, { useEffect, useState } from 'react';
import { Button } from '../components/shared/ui/button';
import { Card, CardContent, CardHeader, CardTitle, CardDescription } from '../components/shared/ui/card';
import { AlertCircle, CheckCircle2, ShieldCheck } from 'lucide-react';
import { GoogleIcon } from '../components/shared/ui/GoogleIcon';
import { authApi, InvitePreviewResponse } from '../services/authApi';

const inputClass =
  'w-full rounded-md border border-input bg-transparent px-3 py-2 text-sm shadow-xs outline-none focus-visible:border-ring focus-visible:ring-ring/50 focus-visible:ring-[3px]';

/**
 * The real page a new teammate lands on after clicking an invite email's
 * link (auth_api.py's create_invite builds accept_url as
 * "{FRONTEND_BASE_URL}/accept-invite?token=..."). Found missing in the
 * credential-provisioning audit: the backend accept-invite endpoint always
 * existed, but nothing in the app rendered this path - hitting it just
 * showed the normal login screen with the token silently ignored.
 *
 * No auth required to view this page (App.tsx renders it standalone,
 * before the normal AuthProvider Gate) - the whole point is that the
 * invitee doesn't have an account yet.
 */
export const AcceptInvitePage: React.FC<{ token: string }> = ({ token }) => {
  const [preview, setPreview] = useState<InvitePreviewResponse | null>(null);
  const [previewError, setPreviewError] = useState<string | null>(null);
  const [isLoadingPreview, setIsLoadingPreview] = useState(true);

  const [username, setUsername] = useState('');
  const [password, setPassword] = useState('');
  const [acceptError, setAcceptError] = useState<string | null>(null);
  const [isAccepting, setIsAccepting] = useState(false);
  const [accepted, setAccepted] = useState<string | null>(null);

  useEffect(() => {
    authApi
      .previewInvite(token)
      .then(setPreview)
      .catch((err) => setPreviewError(err instanceof Error ? err.message : 'Invite not found'))
      .finally(() => setIsLoadingPreview(false));
  }, [token]);

  const handleAccept = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!username.trim() || !password) return;
    setAcceptError(null);
    setIsAccepting(true);
    try {
      const account = await authApi.acceptInvite(token, username.trim(), password);
      setAccepted(account.username);
    } catch (err) {
      setAcceptError(err instanceof Error ? err.message : 'Could not accept invite');
    } finally {
      setIsAccepting(false);
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
          <CardTitle className="text-xl">You're invited</CardTitle>
          {preview && (
            <CardDescription>
              Join <span className="font-medium text-slate-700">{preview.tenant_id}</span> as{' '}
              <span className="font-medium text-slate-700">{preview.role}</span>.
            </CardDescription>
          )}
        </CardHeader>
        <CardContent>
          {isLoadingPreview && <p className="text-sm text-slate-500">Checking invite...</p>}

          {!isLoadingPreview && previewError && (
            <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
              <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
              <span>This invite link is invalid, expired, or has already been used. Ask whoever invited you to send a new one.</span>
            </div>
          )}

          {!isLoadingPreview && preview && !accepted && (
            <div className="space-y-4">
              <a
                href={`/api/auth/oidc/login?invite_token=${encodeURIComponent(token)}`}
                className="flex w-full items-center justify-center gap-2 rounded-md border border-slate-300 bg-white px-4 py-2 text-sm font-medium text-slate-700 hover:bg-slate-50 transition-colors"
              >
                <GoogleIcon className="h-4 w-4" />
                Sign in with Google
              </a>

              <div className="relative py-1">
                <div className="absolute inset-0 flex items-center">
                  <span className="w-full border-t border-slate-200" />
                </div>
                <div className="relative flex justify-center text-xs">
                  <span className="bg-white px-2 text-slate-400">or set a password</span>
                </div>
              </div>

              <form onSubmit={handleAccept} className="space-y-4">
                <div className="space-y-1.5">
                  <label htmlFor="accept-username" className="text-sm font-medium text-slate-700">
                    Username
                  </label>
                  <input
                    id="accept-username"
                    type="text"
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                    placeholder="3-64 characters: letters, numbers, . _ -"
                    className={inputClass}
                  />
                </div>

                <div className="space-y-1.5">
                  <label htmlFor="accept-password" className="text-sm font-medium text-slate-700">
                    Password
                  </label>
                  <input
                    id="accept-password"
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                    autoComplete="new-password"
                    placeholder="At least 8 characters"
                    className={inputClass}
                  />
                </div>

                {acceptError && (
                  <div className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 px-3 py-2 text-sm text-red-700">
                    <AlertCircle className="h-4 w-4 mt-0.5 shrink-0" />
                    <span>{acceptError}</span>
                  </div>
                )}

                <Button type="submit" className="w-full" disabled={isAccepting || !username.trim() || !password}>
                  {isAccepting ? 'Creating account...' : 'Create account and join'}
                </Button>
              </form>
            </div>
          )}

          {accepted && (
            <div className="space-y-4">
              <div className="flex items-start gap-2 rounded-md border border-green-200 bg-green-50 px-3 py-2 text-sm text-green-700">
                <CheckCircle2 className="h-4 w-4 mt-0.5 shrink-0" />
                <span>Account '{accepted}' created. Sign in to continue.</span>
              </div>
              <Button className="w-full" onClick={() => window.location.assign('/')}>
                Continue to sign in
              </Button>
            </div>
          )}
        </CardContent>
      </Card>
    </div>
  );
};
