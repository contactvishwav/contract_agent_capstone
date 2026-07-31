import React from 'react';
import { ThemeProvider } from './components/shared/theme-provider';
import { Navigation } from './components/layout/Navigation';
import { ChatPage } from './pages/ChatPage';
import { IntelligencePage } from './pages/IntelligencePage';
import { DocumentationPage } from './pages/DocumentationPage';
import { SearchPage } from './pages/SearchPage';
import { ErrorBoundary } from './components/shared/ErrorBoundary';
import { ContractHistoryProvider } from './contexts/ContractHistoryContext';
import { AuthProvider, useAuth } from './contexts/AuthContext';
import { LoginScreen } from './components/features/auth/LoginScreen';
import { useRouter } from './lib/useRouter';
import './App.css';

function AuthenticatedApp() {
  const { currentPage, navigate } = useRouter();

  const renderPage = () => {
    switch (currentPage) {
      case 'chat':
        return <ChatPage />;
      case 'intelligence':
        return <IntelligencePage />;
      case 'agents':
        return <DocumentationPage />;
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
  const { isAuthenticated } = useAuth();
  return isAuthenticated ? <AuthenticatedApp /> : <LoginScreen />;
}

function App() {
  return (
    <AuthProvider>
      <ContractHistoryProvider>
        <ThemeProvider defaultTheme="light" storageKey="vite-ui-theme">
          <Gate />
        </ThemeProvider>
      </ContractHistoryProvider>
    </AuthProvider>
  );
}

export default App;