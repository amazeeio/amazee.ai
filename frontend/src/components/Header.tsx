import React from 'react';
import { Link, useLocation } from 'react-router-dom';
import { useAuth } from '../contexts/AuthContext';

const linkClasses = "inline-flex items-center px-3 py-1 border border-transparent text-sm font-medium rounded-md text-indigo-700 bg-indigo-100 hover:bg-indigo-200";
const activeClass = "bg-indigo-200";

export const Header: React.FC = () => {
  const { user, logout } = useAuth();
  const location = useLocation();

  return (
    <header className="bg-white shadow">
      <div className="max-w-7xl mx-auto px-4 sm:px-6 lg:px-8">
        <div className="flex justify-between h-16">
          <div className="flex">
            <Link to="/" className="flex items-center text-xl font-bold text-gray-900">
              Private AI as a Service
            </Link>
          </div>
          <div className="flex items-center space-x-4">
            <span className="text-gray-700">Welcome, {user?.email}</span>
            <Link
              to="/api-tokens"
              className={`${linkClasses} ${location.pathname === '/api-tokens' ? activeClass : ''}`}
            >
              API Tokens
            </Link>
            {user?.is_admin && (
              <>
                <Link
                  to="/admin"
                  className={`${linkClasses} ${location.pathname === '/admin' ? activeClass : ''}`}
                >
                  Admin
                </Link>
                <Link
                  to="/audit-logs"
                  className={`${linkClasses} ${location.pathname === '/audit-logs' ? activeClass : ''}`}
                >
                  Audit Logs
                </Link>
              </>
            )}
            <button
              onClick={logout}
              className="inline-flex items-center px-3 py-1 border border-transparent text-sm font-medium rounded-md text-red-700 bg-red-100 hover:bg-red-200"
            >
              Logout
            </button>
          </div>
        </div>
      </div>
    </header>
  );
};