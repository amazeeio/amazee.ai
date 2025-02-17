import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { tokens, APIToken } from '../api/client';
import { Header } from '../components/Header';

export const APITokens: React.FC = () => {
  const [newTokenName, setNewTokenName] = useState('');
  const [showNewToken, setShowNewToken] = useState<APIToken | null>(null);
  const queryClient = useQueryClient();

  const { data: tokensList = [], isLoading } = useQuery({
    queryKey: ['tokens'],
    queryFn: tokens.list,
  });

  const createMutation = useMutation({
    mutationFn: tokens.create,
    onSuccess: (newToken) => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] });
      setShowNewToken(newToken);
      setNewTokenName('');
    },
  });

  const deleteMutation = useMutation({
    mutationFn: tokens.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['tokens'] });
    },
  });

  const handleCreateToken = async (e: React.FormEvent) => {
    e.preventDefault();
    if (newTokenName.trim()) {
      await createMutation.mutateAsync(newTokenName);
    }
  };

  const handleDeleteToken = async (tokenId: number) => {
    if (window.confirm('Are you sure you want to delete this token? This action cannot be undone.')) {
      await deleteMutation.mutateAsync(tokenId);
    }
  };

  if (isLoading) {
    return <div>Loading...</div>;
  }

  return (
    <div className="min-h-screen bg-gray-100">
      <Header />
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <div className="px-4 py-6 sm:px-0">
          <h2 className="text-2xl font-bold mb-6">API Tokens</h2>

          {/* New Token Form */}
          <form onSubmit={handleCreateToken} className="mb-8">
            <div className="flex gap-4">
              <input
                type="text"
                value={newTokenName}
                onChange={(e) => setNewTokenName(e.target.value)}
                placeholder="Token name"
                className="appearance-none block w-full px-3 py-2 border border-gray-300 rounded-md shadow-sm placeholder-gray-400 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
              />
              <button
                type="submit"
                disabled={createMutation.isPending || !newTokenName.trim()}
                className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 disabled:opacity-50 disabled:cursor-not-allowed"
              >
                {createMutation.isPending ? 'Creating...' : 'Create Token'}
              </button>
            </div>
          </form>

          {/* Show New Token */}
          {showNewToken && (
            <div className="mb-8 p-4 bg-green-50 rounded-md">
              <div className="flex justify-between items-start">
                <div>
                  <h3 className="text-green-800 font-medium">New Token Created</h3>
                  <p className="text-sm text-green-700 mt-1">
                    Make sure to copy your token now. You won't be able to see it again!
                  </p>
                </div>
                <button
                  onClick={() => setShowNewToken(null)}
                  className="text-green-700 hover:text-green-900"
                >
                  ×
                </button>
              </div>
              <div className="mt-4">
                <code className="block p-2 bg-white rounded border border-green-200 text-sm">
                  {showNewToken.token}
                </code>
              </div>
            </div>
          )}

          {/* Tokens List */}
          <div className="bg-white shadow overflow-hidden sm:rounded-md">
            <ul className="divide-y divide-gray-200">
              {tokensList.map((token) => (
                <li key={token.id} className="px-4 py-4">
                  <div className="flex items-center justify-between">
                    <div>
                      <h3 className="text-lg font-medium text-gray-900">{token.name}</h3>
                      <p className="text-sm text-gray-500">
                        Created: {new Date(token.created_at).toLocaleDateString()}
                      </p>
                      {token.last_used_at && (
                        <p className="text-sm text-gray-500">
                          Last used: {new Date(token.last_used_at).toLocaleDateString()}
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeleteToken(token.id)}
                      disabled={deleteMutation.isPending}
                      className="inline-flex items-center px-3 py-1 border border-transparent text-sm font-medium rounded-md text-red-700 bg-red-100 hover:bg-red-200 disabled:opacity-50 disabled:cursor-not-allowed"
                    >
                      {deleteMutation.isPending ? 'Deleting...' : 'Delete'}
                    </button>
                  </div>
                </li>
              ))}
              {tokensList.length === 0 && (
                <li className="px-4 py-4 text-center text-gray-500">
                  No API tokens found. Create one to get started.
                </li>
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};