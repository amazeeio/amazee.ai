import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { privateAIKeys, regions, PrivateAIKey, Region } from '../api/client';
import { Header } from '../components/Header';
import { LoadingSpinner } from '../components/LoadingSpinner';

export const Dashboard: React.FC = () => {
  const queryClient = useQueryClient();
  const [isCreating, setIsCreating] = useState(false);
  const [selectedRegionId, setSelectedRegionId] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [visibleCredentials, setVisibleCredentials] = useState<Set<string>>(new Set());

  const { data: privateAIKeyList = [], isLoading: isLoadingPrivateAIKeys } = useQuery({
    queryKey: ['privateAIKeys'],
    queryFn: privateAIKeys.list,
  });

  const { data: regionsList = [], isLoading: isLoadingRegions } = useQuery({
    queryKey: ['regions'],
    queryFn: regions.list,
  });

  const createMutation = useMutation({
    mutationFn: (regionId: number) => privateAIKeys.create({ region_id: regionId }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['privateAIKeys'] });
      setIsCreating(false);
      setSelectedRegionId(null);
      setError(null);
    },
    onError: (err: any) => {
      console.log('Create mutation error:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to create Private AI Key';
      console.log('Setting error to:', errorMessage);
      setError(errorMessage);
    },
  });

  const deleteMutation = useMutation({
    mutationFn: privateAIKeys.delete,
    onSuccess: (_: unknown, keyId: string) => {
      queryClient.invalidateQueries({ queryKey: ['privateAIKeys'] });
      setError(null);
    },
    onError: (err: any, keyId: string) => {
      console.log('Delete mutation error:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to delete Private AI Key';
      console.log('Setting error to:', errorMessage);
      setError(errorMessage);
    },
  });

  const handleCreatePrivateAIKey = async () => {
    if (!selectedRegionId) {
      console.log('Setting region selection error');
      setError('Please select a region');
      return;
    }
    try {
      setError(null);
      await createMutation.mutateAsync(selectedRegionId);
    } catch (err: any) {
      console.log('Create handler error:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to create Private AI Key';
      console.log('Setting error to:', errorMessage);
      setError(errorMessage);
    }
  };

  const handleDeletePrivateAIKey = async (keyId: string) => {
    if (!window.confirm('Are you sure you want to delete this Private AI Key?')) {
      return;
    }
    try {
      setError(null);
      await deleteMutation.mutateAsync(keyId);
    } catch (err: any) {
      console.log('Delete handler error:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to delete Private AI Key';
      console.log('Setting error to:', errorMessage);
      setError(errorMessage);
    }
  };

  if (isLoadingPrivateAIKeys || isLoadingRegions) {
    return (
      <>
        <Header />
        <LoadingSpinner fullScreen />
      </>
    );
  }

  const activeRegions = regionsList.filter((region: Region) => region.is_active);

  return (
    <div className="min-h-screen bg-gray-100">
      <Header />
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <div className="px-4 py-6 sm:px-0">
          {error && (
            <div className="mb-4 bg-red-50 border border-red-200 text-red-800 rounded-md p-4">
              <div className="flex">
                <div className="flex-shrink-0">
                  <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                    <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                  </svg>
                </div>
                <div className="ml-3">
                  <p className="text-sm">{error}</p>
                </div>
                <div className="ml-auto pl-3">
                  <div className="-mx-1.5 -my-1.5">
                    <button
                      onClick={() => setError(null)}
                      className="inline-flex rounded-md p-1.5 text-red-500 hover:bg-red-100 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-red-500"
                    >
                      <span className="sr-only">Dismiss</span>
                      <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                        <path fillRule="evenodd" d="M4.293 4.293a1 1 0 011.414 0L10 8.586l4.293-4.293a1 1 0 111.414 1.414L11.414 10l4.293 4.293a1 1 0 01-1.414 1.414L10 11.414l-4.293 4.293a1 1 0 01-1.414-1.414L8.586 10 4.293 5.707a1 1 0 010-1.414z" clipRule="evenodd" />
                      </svg>
                    </button>
                  </div>
                </div>
              </div>
            </div>
          )}

          <div className="mb-6 flex items-center gap-4">
            {!isCreating ? (
              <button
                onClick={() => setIsCreating(true)}
                className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700"
              >
                Create New Private AI Key
              </button>
            ) : (
              <div className="flex items-center gap-4">
                <select
                  value={selectedRegionId || ''}
                  onChange={(e) => setSelectedRegionId(Number(e.target.value))}
                  className="block w-64 pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                >
                  <option value="">Select a region</option>
                  {activeRegions.map((region: Region) => (
                    <option key={region.id} value={region.id}>
                      {region.name}
                    </option>
                  ))}
                </select>
                <button
                  onClick={handleCreatePrivateAIKey}
                  className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700"
                  disabled={createMutation.isPending || !selectedRegionId}
                >
                  {createMutation.isPending ? 'Creating...' : 'Create Private AI Key'}
                </button>
                <button
                  onClick={() => {
                    setIsCreating(false);
                    setSelectedRegionId(null);
                  }}
                  className="inline-flex items-center px-4 py-2 border border-gray-300 text-sm font-medium rounded-md text-gray-700 bg-white hover:bg-gray-50"
                >
                  Cancel
                </button>
              </div>
            )}
          </div>

          <div className="bg-white shadow overflow-hidden sm:rounded-md">
            <ul className="divide-y divide-gray-200">
              {privateAIKeyList.map((key: PrivateAIKey) => (
                <li key={key.database_name} className="px-6 py-4">
                  <div className="flex items-center justify-between">
                    <div className="space-y-2">
                      <h3 className="text-lg font-medium text-gray-900">{key.database_name}</h3>
                      <p className="text-sm text-gray-600">Host: {key.host}</p>
                      <p className="text-sm text-gray-600">Username: {key.username}</p>
                      {key.password && (
                        <div className="flex items-center gap-2 text-sm text-gray-600">
                          <span>Password:</span>
                          {visibleCredentials.has(`${key.database_name}-password`) ? (
                            <div className="flex items-center gap-2">
                              <code className="font-mono bg-gray-100 px-2 py-1 rounded">
                                {key.password}
                              </code>
                              <button
                                onClick={() => {
                                  setVisibleCredentials(prev => {
                                    const next = new Set(prev);
                                    next.delete(`${key.database_name}-password`);
                                    return next;
                                  });
                                }}
                                className="text-gray-400 hover:text-gray-600"
                              >
                                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                  <path d="M10 12a2 2 0 100-4 2 2 0 000 4z" />
                                  <path fillRule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clipRule="evenodd" />
                                </svg>
                              </button>
                            </div>
                          ) : (
                            <button
                              onClick={() => {
                                setVisibleCredentials(prev => {
                                  const next = new Set(prev);
                                  next.add(`${key.database_name}-password`);
                                  return next;
                                });
                              }}
                              className="text-gray-400 hover:text-gray-600"
                            >
                              <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                <path fillRule="evenodd" d="M3.707 2.293a1 1 0 00-1.414 1.414l14 14a1 1 0 001.414-1.414l-1.473-1.473A10.014 10.014 0 0019.542 10C18.268 5.943 14.478 3 10 3a9.958 9.958 0 00-4.512 1.074l-1.78-1.781zm4.261 4.26l1.514 1.515a2.003 2.003 0 012.45 2.45l1.514 1.514a4 4 0 00-5.478-5.478z" clipRule="evenodd" />
                                <path d="M12.454 16.697L9.75 13.992a4 4 0 01-3.742-3.741L2.335 6.578A9.98 9.98 0 00.458 10c1.274 4.057 5.065 7 9.542 7 .847 0 1.669-.105 2.454-.303z" />
                              </svg>
                            </button>
                          )}
                        </div>
                      )}
                      {key.litellm_token && (
                        <>
                          <div className="flex items-center gap-2 text-sm text-gray-600">
                            <span>LiteLLM API URL:</span>
                            <code className="font-mono bg-gray-100 px-2 py-1 rounded">
                              {key.litellm_api_url}
                            </code>
                          </div>
                          <div className="flex items-center gap-2 text-sm text-gray-600">
                            <span>LiteLLM Token:</span>
                            {visibleCredentials.has(`${key.database_name}-token`) ? (
                              <div className="flex items-center gap-2">
                                <code className="font-mono bg-gray-100 px-2 py-1 rounded">
                                  {key.litellm_token}
                                </code>
                                <button
                                  onClick={() => {
                                    setVisibleCredentials(prev => {
                                      const next = new Set(prev);
                                      next.delete(`${key.database_name}-token`);
                                      return next;
                                    });
                                  }}
                                  className="text-gray-400 hover:text-gray-600"
                                >
                                  <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                    <path d="M10 12a2 2 0 100-4 2 2 0 000 4z" />
                                    <path fillRule="evenodd" d="M.458 10C1.732 5.943 5.522 3 10 3s8.268 2.943 9.542 7c-1.274 4.057-5.064 7-9.542 7S1.732 14.057.458 10zM14 10a4 4 0 11-8 0 4 4 0 018 0z" clipRule="evenodd" />
                                  </svg>
                                </button>
                              </div>
                            ) : (
                              <button
                                onClick={() => {
                                  setVisibleCredentials(prev => {
                                    const next = new Set(prev);
                                    next.add(`${key.database_name}-token`);
                                    return next;
                                  });
                                }}
                                className="text-gray-400 hover:text-gray-600"
                              >
                                <svg className="h-5 w-5" viewBox="0 0 20 20" fill="currentColor">
                                  <path fillRule="evenodd" d="M3.707 2.293a1 1 0 00-1.414 1.414l14 14a1 1 0 001.414-1.414l-1.473-1.473A10.014 10.014 0 0019.542 10C18.268 5.943 14.478 3 10 3a9.958 9.958 0 00-4.512 1.074l-1.78-1.781zm4.261 4.26l1.514 1.515a2.003 2.003 0 012.45 2.45l1.514 1.514a4 4 0 00-5.478-5.478z" clipRule="evenodd" />
                                  <path d="M12.454 16.697L9.75 13.992a4 4 0 01-3.742-3.741L2.335 6.578A9.98 9.98 0 00.458 10c1.274 4.057 5.065 7 9.542 7 .847 0 1.669-.105 2.454-.303z" />
                                </svg>
                              </button>
                            )}
                          </div>
                        </>
                      )}
                      {key.region && (
                        <p className="text-sm text-gray-600">
                          Region:{' '}
                          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                            {key.region}
                          </span>
                        </p>
                      )}
                    </div>
                    <button
                      onClick={() => handleDeletePrivateAIKey(key.id)}
                      className="inline-flex items-center px-3 py-1 border border-transparent text-sm font-medium rounded-md text-red-700 bg-red-100 hover:bg-red-200"
                    >
                      Delete
                    </button>
                  </div>
                </li>
              ))}
              {privateAIKeyList.length === 0 && (
                <li className="px-6 py-4 text-center text-gray-500">No Private AI Keys found</li>
              )}
            </ul>
          </div>
        </div>
      </div>
    </div>
  );
};