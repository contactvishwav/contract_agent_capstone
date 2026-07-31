import React from 'react';
import { Button } from '../shared/ui/button';
import { UserCircle2, LogOut } from 'lucide-react';
import { useAuth } from '../../contexts/AuthContext';

interface NavigationProps {
  currentPage: 'chat' | 'intelligence' | 'agents' | 'search';
  onNavigate: (page: 'chat' | 'intelligence' | 'agents' | 'search') => void;
}

export const Navigation: React.FC<NavigationProps> = ({ currentPage, onNavigate }) => {
  const { session, logout } = useAuth();

  return (
    <nav className="mb-8 border-b border-slate-200 bg-white rounded-lg shadow-sm">
      <div className="px-6 py-4">
        <div className="flex items-center justify-between">
          <div className="flex items-center space-x-8">
            <h1 className="text-2xl font-bold text-slate-800">Contract Intelligence</h1>
            <div className="flex space-x-1">
              <Button
                variant={currentPage === 'intelligence' ? 'default' : 'ghost'}
                onClick={() => onNavigate('intelligence')}
                className={`px-4 py-2 ${currentPage === 'intelligence' ? 'bg-blue-600 hover:bg-blue-700 text-white' : 'bg-blue-50 hover:bg-blue-100 text-blue-700 border border-blue-200'}`}
              >
                Document Analysis
              </Button>
              <Button
                variant={currentPage === 'chat' ? 'default' : 'ghost'}
                onClick={() => onNavigate('chat')}
                className={`px-4 py-2 ${currentPage === 'chat' ? 'bg-green-600 hover:bg-green-700 text-white' : 'bg-green-50 hover:bg-green-100 text-green-700 border border-green-200'}`}
              >
                Contract Chat
              </Button>
              <Button
                variant={currentPage === 'search' ? 'default' : 'ghost'}
                onClick={() => onNavigate('search')}
                className={`px-4 py-2 ${currentPage === 'search' ? 'bg-teal-600 hover:bg-teal-700 text-white' : 'bg-teal-50 hover:bg-teal-100 text-teal-700 border border-teal-200'}`}
              >
                Enhanced Search
              </Button>
              <Button
                variant={currentPage === 'agents' ? 'default' : 'ghost'}
                onClick={() => onNavigate('agents')}
                className={`px-4 py-2 ${currentPage === 'agents' ? 'bg-purple-600 hover:bg-purple-700 text-white' : 'bg-purple-50 hover:bg-purple-100 text-purple-700 border border-purple-200'}`}
              >
                Documentation
              </Button>
            </div>
          </div>
          {session && (
            <div className="flex items-center gap-3">
              <div className="flex items-center gap-1.5 text-sm text-slate-600">
                <UserCircle2 className="h-4 w-4 text-slate-400" />
                <span>
                  Logged in as <span className="font-medium text-slate-800">{session.tenantId}</span>
                  {' · '}
                  <span className="font-medium text-slate-800">{session.role}</span>
                </span>
              </div>
              <Button
                variant="ghost"
                size="sm"
                onClick={logout}
                className="text-slate-500 hover:text-red-600 hover:bg-red-50"
              >
                <LogOut className="h-3.5 w-3.5 mr-1.5" />
                Log out
              </Button>
            </div>
          )}
        </div>
      </div>
    </nav>
  );
};