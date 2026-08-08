import { ThemeProvider } from './components/shared/theme-provider';
import { Navigation } from './components/layout/Navigation';
import { ChatPage } from './pages/ChatPage';
import { IntelligencePage } from './pages/IntelligencePage';
import { DocumentationPage } from './pages/DocumentationPage';
import { SearchPage } from './pages/SearchPage';
import { AccountPage } from './pages/AccountPage';
import { AcceptInvitePage } from './pages/AcceptInvitePage';
import { ErrorBoundary } from './components/shared/ErrorBoundary';
import { ContractHistoryProvider } from './contexts/ContractHistoryContext';
import { ChatSessionProvider } from './contexts/ChatSessionContext';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { LoginScreen } from './components/features/auth/LoginScreen';
import { ChatProvider } from './components/features/contracts/provider';
import { useRouter } from './lib/useRouter';
import './App.css';

function AuthenticatedApp() {
  const { currentPage, navigate } = useRouter();

  const renderPage = () => {
    switch (currentPage) {
      case 'chat':
        // Real, confirmed bug found live: Contract Chat had no
        // ErrorBoundary at all, unlike 'search' below - a render crash
        // here (e.g. from an unexpected message content shape - see
        // main.py's _normalize_ai_message_content for the root cause
        // that produced it) took down the entire app with nothing to
        // catch it, reproducing as a blank white page. This boundary is
        // real defense-in-depth, independent of that root-cause fix -
        // this class of shape-mismatch should never be able to do that
        // again, regardless of what introduces the next one.
        return (
          <ErrorBoundary>
            <ChatPage />
          </ErrorBoundary>
        );
      case 'intelligence':
        return <IntelligencePage />;
      case 'agents':
        return <DocumentationPage />;
      case 'account':
        return <AccountPage />;
      case 'search':
        return (
          <ErrorBoundary>
            <SearchPage />
          </ErrorBoundary>
        );
      default:
        return <IntelligencePage />;
    }
  };

  return (
    <div className="min-h-screen bg-slate-50">
      <div className="mx-auto max-w-7xl p-6">
        <Navigation currentPage={currentPage} onNavigate={navigate} />
        {renderPage()}
      </div>
    </div>
  );
}

function Gate() {
  const { session } = useAuth();
  if (!session) return <LoginScreen />;

  // Mount every tenant-owned client state container inside the auth gate
  // and key the subtree by the validated token's tenant claim. Logout or
  // tenant switch therefore unmounts stale chat/contract state before a
  // different tenant can render it.
  return (
    <ContractHistoryProvider key={session.tenantId}>
      <ChatProvider>
        <ChatSessionProvider>
          <AuthenticatedApp />
        </ChatSessionProvider>
      </ChatProvider>
    </ContractHistoryProvider>
  );
}

function App() {
  // The invite-accept link (auth_api.py's create_invite builds it as
  // "{FRONTEND_BASE_URL}/accept-invite?token=...") needs to render before
  // the normal auth Gate below - the whole point of this page is that its
  // visitor doesn't have a session yet. No client-side router exists in
  // this app (useRouter.ts is in-memory page-switching, not URL-based), so
  // this is a deliberately minimal, one-off path check rather than pulling
  // in a routing library for a single real route.
  const params = new URLSearchParams(window.location.search);
  const inviteToken = window.location.pathname === '/accept-invite' ? params.get('token') : null;
  if (inviteToken) {
    return <AcceptInvitePage token={inviteToken} />;
  }

  return (
    <AuthProvider>
      <ThemeProvider defaultTheme="light" storageKey="vite-ui-theme">
        <Gate />
      </ThemeProvider>
    </AuthProvider>
  );
}

export default App;
