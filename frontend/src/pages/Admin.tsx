import React, { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useAuth } from '../contexts/AuthContext';
import { users, privateAIKeys, regions, User, PrivateAIKey, RegionCreate } from '../api/client';
import { Navigate } from 'react-router-dom';
import { Header } from '../components/Header';
import { LoadingSpinner } from '../components/LoadingSpinner';

export const Admin: React.FC = () => {
  const { user } = useAuth();
  const queryClient = useQueryClient();
  const [isAddingRegion, setIsAddingRegion] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [visibleCredentials, setVisibleCredentials] = useState<Set<string>>(new Set());
  const [newRegion, setNewRegion] = useState<RegionCreate>({
    name: '',
    postgres_host: '',
    postgres_port: 5432,
    postgres_admin_user: '',
    postgres_admin_password: '',
    litellm_api_url: '',
    litellm_api_key: '',
    postgres_db: '',
  });
  const [isAddingUser, setIsAddingUser] = useState(false);
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');

  const { data: usersList = [], isLoading: isLoadingUsers } = useQuery({
    queryKey: ['users'],
    queryFn: users.list,
  });

  const { data: privateAIKeysList = [], isLoading: isLoadingPrivateAIKeys } = useQuery({
    queryKey: ['privateAIKeys'],
    queryFn: privateAIKeys.list,
  });

  const { data: regionsList = [], isLoading: isLoadingRegions } = useQuery({
    queryKey: ['regions'],
    queryFn: regions.list,
  });

  const updateUserMutation = useMutation({
    mutationFn: ({ userId, isAdmin }: { userId: number; isAdmin: boolean }) =>
      users.update(userId, { is_admin: isAdmin }),
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
    },
  });

  const createRegionMutation = useMutation({
    mutationFn: regions.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regions'] });
      setIsAddingRegion(false);
      setNewRegion({
        name: '',
        postgres_host: '',
        postgres_port: 5432,
        postgres_admin_user: '',
        postgres_admin_password: '',
        litellm_api_url: '',
        litellm_api_key: '',
        postgres_db: '',
      });
    },
  });

  const deleteRegionMutation = useMutation({
    mutationFn: regions.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regions'] });
      setError(null);
    },
    onError: (err: any) => {
      console.error('Failed to delete region:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to delete region';
      setError(errorMessage);
    },
  });

  const deletePrivateAIKeyMutation = useMutation({
    mutationFn: privateAIKeys.delete,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['privateAIKeys'] });
      setError(null);
    },
    onError: (err: any) => {
      console.error('Failed to delete private AI key:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to delete private AI key';
      setError(errorMessage);
    },
  });

  const registerUserMutation = useMutation({
    mutationFn: users.create,
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      setIsAddingUser(false);
      setNewUserEmail('');
      setNewUserPassword('');
    },
    onError: (err: any) => {
      console.error('Failed to create user:', err);
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to create user';
      setError(errorMessage);
    },
  });

  // Redirect if not admin
  if (isLoadingUsers || isLoadingPrivateAIKeys || isLoadingRegions) {
    return (
      <>
        <Header />
        <LoadingSpinner fullScreen />
      </>
    );
  }

  if (!user?.is_admin) {
    return <Navigate to="/" />;
  }

  const handleToggleAdmin = async (userId: number, currentIsAdmin: boolean) => {
    try {
      await updateUserMutation.mutateAsync({
        userId,
        isAdmin: !currentIsAdmin,
      });
    } catch (error) {
      console.error('Failed to update user:', error);
    }
  };

  const handleCreateRegion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!newRegion.name || !newRegion.postgres_host || !newRegion.postgres_port) {
      setError('Please fill in all required fields');
      return;
    }

    try {
      await createRegionMutation.mutateAsync({
        ...newRegion,
        postgres_port: Number(newRegion.postgres_port),
      });
    } catch (err: any) {
      const errorMessage = err?.response?.data?.detail || err?.message || 'Failed to create region';
      setError(errorMessage);
    }
  };

  const handleDeleteRegion = async (regionId: number) => {
    if (window.confirm('Are you sure you want to delete this region?')) {
      try {
        await deleteRegionMutation.mutateAsync(regionId);
      } catch (error: any) {
        const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to delete region';
        setError(errorMessage);
      }
    }
  };

  const handleDeletePrivateAIKey = async (keyName: string) => {
    if (window.confirm('Are you sure you want to delete this private AI key?')) {
      try {
        await deletePrivateAIKeyMutation.mutateAsync(keyName);
      } catch (error: any) {
        const errorMessage = error?.response?.data?.detail || error?.message || 'Failed to delete private AI key';
        setError(errorMessage);
      }
    }
  };

  const handleCreateUser = () => {
    registerUserMutation.mutate({
      email: newUserEmail,
      password: newUserPassword,
    });
  };

  return (
    <div className="min-h-screen bg-gray-100">
      <Header />
      <div className="max-w-7xl mx-auto py-6 sm:px-6 lg:px-8">
        <h1 className="text-3xl font-bold text-gray-900 mb-8">Admin Dashboard</h1>

        {error && (
          <div className="mb-4 bg-red-50 border border-red-200 text-red-800 rounded-md p-4" role="alert">
            <div className="flex">
              <div className="flex-shrink-0">
                <svg className="h-5 w-5 text-red-400" viewBox="0 0 20 20" fill="currentColor">
                  <path fillRule="evenodd" d="M10 18a8 8 0 100-16 8 8 0 000 16zM8.707 7.293a1 1 0 00-1.414 1.414L8.586 10l-1.293 1.293a1 1 0 101.414 1.414L10 11.414l1.293 1.293a1 1 0 001.414-1.414L11.414 10l1.293-1.293a1 1 0 00-1.414-1.414L10 8.586 8.707 7.293z" clipRule="evenodd" />
                </svg>
              </div>
              <div className="ml-3">
                <p className="text-sm" data-testid="error-message">{error}</p>
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

        {/* Regions Section */}
        <div className="bg-white shadow overflow-hidden sm:rounded-lg mb-8">
          <div className="px-4 py-5 sm:px-6 flex justify-between items-center">
            <h2 className="text-xl font-semibold text-gray-900">Regions</h2>
            <button
              onClick={() => setIsAddingRegion(!isAddingRegion)}
              className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700"
            >
              {isAddingRegion ? 'Cancel' : 'Add Region'}
            </button>
          </div>

          {isAddingRegion && (
            <div className="px-4 py-5 sm:px-6 border-t border-gray-200">
              <form onSubmit={handleCreateRegion} className="space-y-4">
                <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
                  <div>
                    <label htmlFor="region-name" className="block text-sm font-medium text-gray-700">Name</label>
                    <input
                      id="region-name"
                      name="name"
                      type="text"
                      value={newRegion.name}
                      onChange={(e) => setNewRegion({ ...newRegion, name: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="Region name"
                    />
                  </div>
                  <div>
                    <label htmlFor="postgres-host" className="block text-sm font-medium text-gray-700">Postgres Host</label>
                    <input
                      id="postgres-host"
                      name="postgres_host"
                      type="text"
                      value={newRegion.postgres_host}
                      onChange={(e) => setNewRegion({ ...newRegion, postgres_host: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="Postgres host"
                    />
                  </div>
                  <div>
                    <label htmlFor="postgres-port" className="block text-sm font-medium text-gray-700">Postgres Port</label>
                    <input
                      id="postgres-port"
                      name="postgres_port"
                      type="number"
                      value={newRegion.postgres_port || ''}
                      onChange={(e) => {
                        const value = e.target.value === '' ? 5432 : parseInt(e.target.value);
                        setNewRegion({ ...newRegion, postgres_port: value });
                      }}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="Postgres port"
                    />
                  </div>
                  <div>
                    <label htmlFor="postgres-db" className="block text-sm font-medium text-gray-700">Postgres Database</label>
                    <input
                      id="postgres-db"
                      name="postgres_db"
                      type="text"
                      value={newRegion.postgres_db}
                      onChange={(e) => setNewRegion({ ...newRegion, postgres_db: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="Postgres database"
                    />
                  </div>
                  <div>
                    <label htmlFor="postgres-admin-user" className="block text-sm font-medium text-gray-700">Postgres Admin User</label>
                    <input
                      id="postgres-admin-user"
                      name="postgres_admin_user"
                      type="text"
                      value={newRegion.postgres_admin_user}
                      onChange={(e) => setNewRegion({ ...newRegion, postgres_admin_user: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="Postgres admin user"
                    />
                  </div>
                  <div>
                    <label htmlFor="postgres-admin-password" className="block text-sm font-medium text-gray-700">Postgres Admin Password</label>
                    <input
                      id="postgres-admin-password"
                      name="postgres_admin_password"
                      type="password"
                      value={newRegion.postgres_admin_password}
                      onChange={(e) => setNewRegion({ ...newRegion, postgres_admin_password: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="Postgres admin password"
                    />
                  </div>
                  <div>
                    <label htmlFor="litellm-api-url" className="block text-sm font-medium text-gray-700">LiteLLM API URL</label>
                    <input
                      id="litellm-api-url"
                      name="litellm_api_url"
                      type="text"
                      value={newRegion.litellm_api_url}
                      onChange={(e) => setNewRegion({ ...newRegion, litellm_api_url: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="LiteLLM API URL"
                    />
                  </div>
                  <div>
                    <label htmlFor="litellm-api-key" className="block text-sm font-medium text-gray-700">LiteLLM API Key</label>
                    <input
                      id="litellm-api-key"
                      name="litellm_api_key"
                      type="password"
                      value={newRegion.litellm_api_key}
                      onChange={(e) => setNewRegion({ ...newRegion, litellm_api_key: e.target.value })}
                      className="mt-1 block w-full border border-gray-300 rounded-md shadow-sm py-2 px-3 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm"
                      required
                      aria-label="LiteLLM API key"
                    />
                  </div>
                </div>
                <div className="flex justify-end">
                  <button
                    type="submit"
                    className="inline-flex justify-center py-2 px-4 border border-transparent shadow-sm text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700 focus:outline-none focus:ring-2 focus:ring-offset-2 focus:ring-indigo-500"
                  >
                    Create Region
                  </button>
                </div>
              </form>
            </div>
          )}

          <div className="border-t border-gray-200">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Name</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Postgres Host</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Status</th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">Actions</th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {regionsList.map((region) => (
                  <tr key={region.id}>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-900">{region.name}</td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">{region.postgres_host}</td>
                    <td className="px-6 py-4 whitespace-nowrap">
                      <span className={`px-2 inline-flex text-xs leading-5 font-semibold rounded-full ${
                        region.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                      }`}>
                        {region.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </td>
                    <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                      <button
                        onClick={() => handleDeleteRegion(region.id)}
                        className="text-red-600 hover:text-red-900"
                        aria-label={`Delete region ${region.name}`}
                      >
                        Delete
                      </button>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </div>

        {/* Users Section */}
        <div className="bg-white shadow overflow-hidden sm:rounded-lg mb-8">
          <div className="px-4 py-5 sm:px-6 flex justify-between items-center">
            <h2 className="text-xl font-semibold text-gray-900">Users</h2>
            <button
              onClick={() => setIsAddingUser(!isAddingUser)}
              className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700"
            >
              {isAddingUser ? 'Cancel' : 'Add User'}
            </button>
          </div>

          {isAddingUser && (
            <div className="px-4 py-5 sm:px-6 border-t border-gray-200">
              <div className="flex items-center gap-4">
                <input
                  type="email"
                  value={newUserEmail}
                  onChange={(e) => setNewUserEmail(e.target.value)}
                  placeholder="Email"
                  className="block w-64 pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                />
                <input
                  type="password"
                  value={newUserPassword}
                  onChange={(e) => setNewUserPassword(e.target.value)}
                  placeholder="Password"
                  className="block w-64 pl-3 pr-10 py-2 text-base border-gray-300 focus:outline-none focus:ring-indigo-500 focus:border-indigo-500 sm:text-sm rounded-md"
                />
                <button
                  onClick={handleCreateUser}
                  className="inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md text-white bg-indigo-600 hover:bg-indigo-700"
                  disabled={registerUserMutation.isPending}
                >
                  {registerUserMutation.isPending ? 'Adding...' : 'Add User'}
                </button>
              </div>
            </div>
          )}

          <ul className="divide-y divide-gray-200">
            {usersList.map((user: User) => (
              <li key={user.id} className="px-6 py-4">
                <div className="flex items-center justify-between">
                  <div>
                    <h3 className="text-lg font-medium text-gray-900">{user.email}</h3>
                    <p className="text-sm text-gray-600">
                      Status: {user.is_active ? 'Active' : 'Inactive'}
                    </p>
                    <p className="text-sm text-gray-600">
                      Role: {user.is_admin ? 'Admin' : 'User'}
                    </p>
                  </div>
                  <div>
                    <button
                      onClick={() => handleToggleAdmin(user.id, user.is_admin)}
                      className={`inline-flex items-center px-4 py-2 border border-transparent text-sm font-medium rounded-md ${
                        user.is_admin
                          ? 'text-red-700 bg-red-100 hover:bg-red-200'
                          : 'text-green-700 bg-green-100 hover:bg-green-200'
                      }`}
                      disabled={updateUserMutation.isPending}
                    >
                      {user.is_admin ? 'Remove Admin' : 'Make Admin'}
                    </button>
                  </div>
                </div>
              </li>
            ))}
            {usersList.length === 0 && (
              <li className="px-6 py-4 text-center text-gray-500">No users found</li>
            )}
          </ul>
        </div>

        {/* Private AI Keys Section */}
        <div className="bg-white shadow overflow-hidden sm:rounded-lg">
          <div className="px-4 py-5 sm:px-6">
            <h2 className="text-xl font-semibold text-gray-900">All Private AI Keys</h2>
          </div>
          <div className="border-t border-gray-200">
            <table className="min-w-full divide-y divide-gray-200">
              <thead className="bg-gray-50">
                <tr>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Key Name
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Host
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Credentials
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Region
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Owner
                  </th>
                  <th className="px-6 py-3 text-left text-xs font-medium text-gray-500 uppercase tracking-wider">
                    Actions
                  </th>
                </tr>
              </thead>
              <tbody className="bg-white divide-y divide-gray-200">
                {privateAIKeysList.map((key: PrivateAIKey) => {
                  const owner = usersList.find(u => u.id === key.owner_id);
                  return (
                    <tr key={key.database_name}>
                      <td className="px-6 py-4 whitespace-nowrap text-sm font-medium text-gray-900">
                        {key.database_name}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {key.host}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <div className="space-y-2">
                          <div className="flex items-center gap-2">
                            <span>Username: {key.username}</span>
                          </div>
                          {key.password && (
                            <div className="flex items-center gap-2">
                              <span>Password:</span>
                              {visibleCredentials.has(`${key.database_name}-password`) ? (
                                <div className="flex items-center gap-2">
                                  <code className="px-2 py-1 bg-gray-100 rounded text-sm font-mono">
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
                            <div className="flex items-center gap-2">
                              <span>LLM Key:</span>
                              {visibleCredentials.has(`${key.database_name}-token`) ? (
                                <div className="flex items-center gap-2">
                                  <code className="px-2 py-1 bg-gray-100 rounded text-sm font-mono">
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
                          )}
                        </div>
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {key.region ? (
                          <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                            {key.region}
                          </span>
                        ) : (
                          <span className="text-gray-400">-</span>
                        )}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        {owner?.email || 'Unknown'}
                      </td>
                      <td className="px-6 py-4 whitespace-nowrap text-sm text-gray-500">
                        <button
                          onClick={() => handleDeletePrivateAIKey(key.database_name)}
                          className="text-red-600 hover:text-red-900"
                          aria-label={`Delete private AI key ${key.database_name}`}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
        </div>
      </div>
    </div>
  );
};