'use client';

import { useState, useEffect, useCallback, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
  TablePagination,
  useTablePagination,
} from '@/components/ui/table';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { useToast } from '@/hooks/use-toast';
import { Loader2, ChevronUp, ChevronDown, ChevronsUpDown, ChevronRight } from 'lucide-react';
import { get, post, del, put } from '@/utils/api';
import { TableActionButtons } from '@/components/ui/table-action-buttons';
import { TableFilters, FilterField } from '@/components/ui/table-filters';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import {
  Collapsible,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import React from 'react';

interface User {
  id: string;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  role: string;
  created_at: string;
  team_name?: string;
}

interface PrivateAIKey {
  id: number;
  name: string;
  database_name: string;
  database_host: string;
  database_username: string;
  database_password: string;
  region: string;
  created_at: string;
  owner_id: number;
  team_id?: number;
  team_name?: string;
  litellm_token?: string;
  litellm_api_url?: string;
}

interface SpendInfo {
  spend: number;
  expires: string;
  created_at: string;
  updated_at: string;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
}

const USER_ROLES = [
  { value: 'admin', label: 'Admin' },
  { value: 'key_creator', label: 'Key Creator' },
  { value: 'read_only', label: 'Read Only' },
  { value: 'sales', label: 'Sales' },
];

type SortField = 'email' | 'team_name' | 'role' | null;
type SortDirection = 'asc' | 'desc';

export default function UsersPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingUser, setIsAddingUser] = useState(false);
  const [isUpdatingRole, setIsUpdatingRole] = useState(false);
  const [selectedUser, setSelectedUser] = useState<{ id: string; currentRole: string } | null>(null);
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState('read_only');
  const [newUserTeamId, setNewUserTeamId] = useState<string>('');
  const [isSystemUser, setIsSystemUser] = useState(false);
  const [users, setUsers] = useState<User[]>([]);
  const [teams, setTeams] = useState<{ id: string; name: string }[]>([]);
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);

  // Filter and sort state
  const [emailFilter, setEmailFilter] = useState('');
  const [teamFilter, setTeamFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  // Queries
  const { isLoading: isLoadingUsers } = useQuery<User[]>({
    queryKey: ['users'],
    queryFn: async () => {
      const response = await get('/users');
      const data = await response.json();
      return data;
    },
  });

  // Get user AI keys when expanded
  const { data: userAIKeys = [], isLoading: isLoadingUserAIKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['user-ai-keys', expandedUserId],
    queryFn: async () => {
      if (!expandedUserId) return [];
      const response = await get(`/private-ai-keys?owner_id=${expandedUserId}`);
      return response.json();
    },
    enabled: !!expandedUserId,
  });

  // Get spend data for each key
  const { data: spendMap = {} } = useQuery<Record<number, SpendInfo>>({
    queryKey: ['user-ai-keys-spend', expandedUserId, userAIKeys],
    queryFn: async () => {
      if (!expandedUserId || userAIKeys.length === 0) return {};

      const spendData: Record<number, SpendInfo> = {};

      for (const key of userAIKeys) {
        try {
          const response = await get(`/private-ai-keys/${key.id}/spend`);
          const spendInfo = await response.json();
          spendData[key.id] = spendInfo;
        } catch (error) {
          console.error(`Failed to fetch spend data for key ${key.id}:`, error);
        }
      }

      return spendData;
    },
    enabled: !!expandedUserId && userAIKeys.length > 0,
  });

  // Fetch teams
  useEffect(() => {
    const fetchTeams = async () => {
      try {
        const response = await get('/teams');
        const data = await response.json();
        setTeams(data);
      } catch (error) {
        console.error('Error fetching teams:', error);
      }
    };
    fetchTeams();
  }, []);

  // Filtered and sorted users
  const filteredAndSortedUsers = useMemo(() => {
    const filtered = users.filter(user => {
      const emailMatch = user.email.toLowerCase().includes(emailFilter.toLowerCase());
      const teamMatch = !teamFilter || (user.team_name || 'None').toLowerCase().includes(teamFilter.toLowerCase());
      const roleMatch = roleFilter === 'all' || user.role === roleFilter;

      return emailMatch && teamMatch && roleMatch;
    });

    if (sortField) {
      filtered.sort((a, b) => {
        let aValue: string;
        let bValue: string;

        switch (sortField) {
          case 'email':
            aValue = a.email.toLowerCase();
            bValue = b.email.toLowerCase();
            break;
          case 'team_name':
            aValue = (a.team_name || 'None').toLowerCase();
            bValue = (b.team_name || 'None').toLowerCase();
            break;
          case 'role':
            aValue = a.role.toLowerCase();
            bValue = b.role.toLowerCase();
            break;
          default:
            return 0;
        }

        if (sortDirection === 'asc') {
          return aValue.localeCompare(bValue);
        } else {
          return bValue.localeCompare(aValue);
        }
      });
    }

    return filtered;
  }, [users, emailFilter, teamFilter, roleFilter, sortField, sortDirection]);

  const hasActiveFilters = Boolean(emailFilter.trim() || teamFilter.trim() || roleFilter !== 'all');

  // Filter fields configuration
  const filterFields: FilterField[] = [
    {
      key: 'email',
      label: 'Filter by Email',
      type: 'search',
      placeholder: 'Search by email...',
      value: emailFilter,
      onChange: setEmailFilter,
    },
    {
      key: 'team',
      label: 'Filter by Team',
      type: 'search',
      placeholder: 'Search by team...',
      value: teamFilter,
      onChange: setTeamFilter,
    },
    {
      key: 'role',
      label: 'Filter by Role',
      type: 'select',
      placeholder: 'All roles',
      value: roleFilter,
      onChange: setRoleFilter,
      options: [
        { value: 'all', label: 'All roles' },
        ...USER_ROLES.map((role) => ({ value: role.value, label: role.label })),
      ],
    },
  ];

  // Pagination
  const {
    currentPage,
    pageSize,
    totalPages,
    totalItems,
    paginatedData,
    goToPage,
    changePageSize,
  } = useTablePagination(filteredAndSortedUsers, 10);

  // Handle sorting
  const handleSort = (field: SortField) => {
    if (sortField === field) {
      setSortDirection(sortDirection === 'asc' ? 'desc' : 'asc');
    } else {
      setSortField(field);
      setSortDirection('asc');
    }
  };

  // Get sort icon
  const getSortIcon = (field: SortField) => {
    if (sortField !== field) {
      return <ChevronsUpDown className="h-4 w-4" />;
    }
    return sortDirection === 'asc' ? <ChevronUp className="h-4 w-4" /> : <ChevronDown className="h-4 w-4" />;
  };

  // Handle user expansion
  const toggleUserExpansion = (userId: string) => {
    if (expandedUserId === userId) {
      setExpandedUserId(null);
    } else {
      setExpandedUserId(userId);
    }
  };



  // Mutations
  const updateUserMutation = useMutation({
    mutationFn: async ({ userId, isAdmin }: { userId: string; isAdmin: boolean }) => {
      const response = await put(`/users/${userId}`, { is_admin: isAdmin });
      const data = await response.json();
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.refetchQueries({ queryKey: ['users'], exact: true });
      toast({
        title: 'Success',
        description: 'User updated successfully',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const updateUserRoleMutation = useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: string }) => {
      const response = await post(`/users/${userId}/role`, { role });
      const data = await response.json();
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.refetchQueries({ queryKey: ['users'], exact: true });
      toast({
        title: 'Success',
        description: 'User role updated successfully',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const createUserMutation = useMutation({
    mutationFn: async (userData: {
      email: string;
      password?: string;
      role?: string;
      team_id?: string;
      is_system_user?: boolean;
    }) => {
      const response = await post('/users', userData);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.refetchQueries({ queryKey: ['users'], exact: true });
      setIsAddingUser(false);
      setNewUserEmail('');
      setNewUserPassword('');
      setNewUserRole('read_only');
      setNewUserTeamId('');
      setIsSystemUser(false);
      toast({
        title: 'Success',
        description: 'User created successfully',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const deleteUserMutation = useMutation({
    mutationFn: async (userId: string) => {
      const response = await del(`/users/${userId}`);
      const data = await response.json();
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
      queryClient.refetchQueries({ queryKey: ['users'], exact: true });
      toast({
        title: 'Success',
        description: 'User deleted successfully',
      });
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const fetchUsers = useCallback(async () => {
    try {
      const response = await get('/users');
      const data = await response.json();
      setUsers(data);
    } catch (error) {
      console.error('Error fetching users:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch users',
        variant: 'destructive',
      });
    }
  }, [toast, setUsers]);

  useEffect(() => {
    void fetchUsers();
  }, [fetchUsers]);

  // Update role when switching between system and team user types
  useEffect(() => {
    if (isSystemUser) {
      setNewUserRole('admin'); // Default to admin for system users
    } else {
      setNewUserRole('read_only'); // Default to read_only for team users
    }
  }, [isSystemUser]);

  if (isLoadingUsers) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  const handleToggleAdmin = (userId: string, currentIsAdmin: boolean) => {
    updateUserMutation.mutate({ userId, isAdmin: !currentIsAdmin });
  };

  const handleUpdateRole = (userId: string, currentRole: string) => {
    setSelectedUser({ id: userId, currentRole });
    setIsUpdatingRole(true);
  };

  const handleConfirmRoleUpdate = (newRole: string) => {
    if (!selectedUser) return;
    updateUserRoleMutation.mutate({ userId: selectedUser.id, role: newRole });
    setIsUpdatingRole(false);
    setSelectedUser(null);
  };

  const handleCreateUser = (e: React.FormEvent) => {
    e.preventDefault();
    const userData: {
      email: string;
      password?: string;
      role?: string;
      team_id?: string;
      is_system_user?: boolean;
    } = {
      email: newUserEmail,
      is_system_user: isSystemUser,
    };

    if (newUserPassword.trim()) {
      userData.password = newUserPassword;
    }

    if (!isSystemUser) {
      userData.role = newUserRole;
      if (newUserTeamId) {
        userData.team_id = newUserTeamId;
      }
    } else {
      // For system users, also set the role
      userData.role = newUserRole;
    }

    createUserMutation.mutate(userData);
  };

  const handleDeleteUser = (userId: string) => {
    deleteUserMutation.mutate(userId);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Users</h1>
        <Dialog open={isAddingUser} onOpenChange={setIsAddingUser}>
          <DialogTrigger asChild>
            <Button>Add User</Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add New User</DialogTitle>
              <DialogDescription>
                Create a new user account. The user will be able to log in with these credentials.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateUser} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Email</label>
                <Input
                  type="email"
                  value={newUserEmail}
                  onChange={(e) => setNewUserEmail(e.target.value)}
                  placeholder="user@example.com"
                  required
                />
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Password</label>
                <Input
                  type="password"
                  value={newUserPassword}
                  onChange={(e) => setNewUserPassword(e.target.value)}
                  placeholder="••••••••"
                />
                <p className="text-xs text-muted-foreground">
                  Leave empty to allow passwordless sign-in (if enabled)
                </p>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">User Type</label>
                <div className="flex items-center space-x-4">
                  <label className="flex items-center space-x-2">
                    <input
                      type="radio"
                      checked={!isSystemUser}
                      onChange={() => setIsSystemUser(false)}
                      className="form-radio"
                    />
                    <span>Team User</span>
                  </label>
                  <label className="flex items-center space-x-2">
                    <input
                      type="radio"
                      checked={isSystemUser}
                      onChange={() => setIsSystemUser(true)}
                      className="form-radio"
                    />
                    <span>System User</span>
                  </label>
                </div>
              </div>
              {!isSystemUser && (
                <>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Team</label>
                    <p>Selected team ID: {newUserTeamId}</p>
                    <Select
                      value={newUserTeamId}
                      onValueChange={value => setNewUserTeamId(String(value))}
                      required
                    >
                      <SelectTrigger className="w-full">
                        <SelectValue placeholder="Select a team" />
                      </SelectTrigger>
                      <SelectContent>
                        {teams.map((team) => (
                          <SelectItem key={team.id} value={String(team.id)}>
                            {team.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Team Role</label>
                    <Select
                      value={newUserRole}
                      onValueChange={setNewUserRole}
                    >
                      <SelectTrigger>
                        <SelectValue placeholder="Select a role" />
                      </SelectTrigger>
                      <SelectContent>
                        {USER_ROLES.filter(role => role.value !== 'sales').map((role) => (
                          <SelectItem key={role.value} value={role.value}>
                            {role.label}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                  </div>
                </>
              )}
              {isSystemUser && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">System Role</label>
                  <Select
                    value={newUserRole}
                    onValueChange={setNewUserRole}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select a system role" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="admin">Admin</SelectItem>
                      <SelectItem value="sales">Sales</SelectItem>
                    </SelectContent>
                  </Select>
                </div>
              )}
              <DialogFooter>
                <Button
                  type="submit"
                  disabled={createUserMutation.isPending}
                >
                  {createUserMutation.isPending ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    'Create User'
                  )}
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <TableFilters
        filters={filterFields}
        onClearFilters={() => {
          setEmailFilter('');
          setTeamFilter('');
          setRoleFilter('all');
          setSortField(null);
          setSortDirection('asc');
        }}
        hasActiveFilters={hasActiveFilters}
        totalItems={users.length}
        filteredItems={filteredAndSortedUsers.length}
      />

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead className="w-10"></TableHead>
              <TableHead>ID</TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('email')}
              >
                <div className="flex items-center gap-2">
                  Email
                  {getSortIcon('email')}
                </div>
              </TableHead>
              <TableHead>Status</TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('team_name')}
              >
                <div className="flex items-center gap-2">
                  Team
                  {getSortIcon('team_name')}
                </div>
              </TableHead>
              <TableHead
                className="cursor-pointer hover:bg-gray-50"
                onClick={() => handleSort('role')}
              >
                <div className="flex items-center gap-2">
                  Team Role
                  {getSortIcon('role')}
                </div>
              </TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {paginatedData.length === 0 ? (
              <TableRow>
                <TableCell colSpan={8} className="text-center py-6">
                  No users found. Create a new user to get started.
                </TableCell>
              </TableRow>
            ) : (
              paginatedData.map((user) => (
                <React.Fragment key={user.id}>
                  <TableRow
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => toggleUserExpansion(user.id)}
                  >
                    <TableCell>
                      {expandedUserId === user.id ? (
                        <ChevronDown className="h-4 w-4" />
                      ) : (
                        <ChevronRight className="h-4 w-4" />
                      )}
                    </TableCell>
                    <TableCell className="font-mono text-sm">{user.id}</TableCell>
                    <TableCell>{user.email}</TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${user.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                        }`}>
                        {user.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </TableCell>
                    <TableCell>
                      {user.team_name || 'None'}
                    </TableCell>
                    <TableCell>
                      {USER_ROLES.find(r => r.value === user.role)?.label || user.role}
                    </TableCell>
                    <TableCell>
                      <div className="flex gap-2">
                        <Button
                          variant={user.is_admin ? "destructive" : "secondary"}
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleToggleAdmin(user.id, user.is_admin);
                          }}
                          disabled={updateUserMutation.isPending}
                        >
                          {user.is_admin ? 'Remove Admin' : 'Make Admin'}
                        </Button>
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={(e) => {
                            e.stopPropagation();
                            handleUpdateRole(user.id, 'member');
                          }}
                          disabled={updateUserRoleMutation.isPending}
                        >
                          Change Role
                        </Button>
                        <TableActionButtons
                          showEdit={false}
                          onDelete={() => handleDeleteUser(user.id)}
                          deleteTitle="Are you sure?"
                          deleteDescription="This action cannot be undone. This will permanently delete the user account and all associated data."
                          isDeleting={deleteUserMutation.isPending}
                        />
                      </div>
                    </TableCell>
                  </TableRow>
                  {expandedUserId === user.id && (
                    <TableRow>
                      <TableCell colSpan={8} className="p-0">
                        <Collapsible open={expandedUserId === user.id}>
                          <CollapsibleContent className="p-4 bg-muted/30">
                            {isLoadingUserAIKeys ? (
                              <div className="flex justify-center items-center py-8">
                                <Loader2 className="h-8 w-8 animate-spin" />
                              </div>
                            ) : (
                              <div className="space-y-4">
                                <h3 className="text-lg font-medium">AI Keys</h3>
                                {userAIKeys.length > 0 ? (
                                  <div className="rounded-md border">
                                    <Table>
                                      <TableHeader>
                                        <TableRow>
                                          <TableHead>Name</TableHead>
                                          <TableHead>Region</TableHead>
                                          <TableHead>Database</TableHead>
                                          <TableHead>Created At</TableHead>
                                          <TableHead>Spend</TableHead>
                                          <TableHead>Budget</TableHead>
                                        </TableRow>
                                      </TableHeader>
                                      <TableBody>
                                        {userAIKeys.map((key) => {
                                          const spendInfo = spendMap[key.id];
                                          return (
                                            <TableRow key={key.id}>
                                              <TableCell>{key.name}</TableCell>
                                              <TableCell>{key.region}</TableCell>
                                              <TableCell>{key.database_name}</TableCell>
                                              <TableCell>
                                                {new Date(key.created_at).toLocaleDateString()}
                                              </TableCell>
                                              <TableCell>
                                                {spendInfo ? (
                                                  <span>
                                                    ${spendInfo.spend.toFixed(2)}
                                                  </span>
                                                ) : (
                                                  <span className="text-muted-foreground">Loading...</span>
                                                )}
                                              </TableCell>
                                              <TableCell>
                                                {spendInfo?.max_budget ? (
                                                  <span>
                                                    ${spendInfo.max_budget.toFixed(2)}
                                                  </span>
                                                ) : (
                                                  <span className="text-muted-foreground">No limit</span>
                                                )}
                                              </TableCell>

                                            </TableRow>
                                          );
                                        })}
                                      </TableBody>
                                    </Table>
                                  </div>
                                ) : (
                                  <div className="text-center py-8 border rounded-md">
                                    <p className="text-muted-foreground">No AI keys found for this user.</p>
                                  </div>
                                )}
                              </div>
                            )}
                          </CollapsibleContent>
                        </Collapsible>
                      </TableCell>
                    </TableRow>
                  )}
                </React.Fragment>
              ))
            )}
          </TableBody>
        </Table>
      </div>

      <TablePagination
        currentPage={currentPage}
        totalPages={totalPages}
        pageSize={pageSize}
        totalItems={totalItems}
        onPageChange={goToPage}
        onPageSizeChange={changePageSize}
      />

      {/* Role Update Dialog */}
      <Dialog open={isUpdatingRole} onOpenChange={setIsUpdatingRole}>
        <DialogContent>
          <DialogHeader>
            <DialogTitle>Update User Role</DialogTitle>
            <DialogDescription>
              Select a new role for this user. This will change their permissions within the system.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <Select
                value={selectedUser?.currentRole || 'read_only'}
                onValueChange={handleConfirmRoleUpdate}
              >
                <SelectTrigger>
                  <SelectValue placeholder="Select a role" />
                </SelectTrigger>
                <SelectContent>
                  {USER_ROLES.map((role) => (
                    <SelectItem key={role.value} value={role.value}>
                      {role.label}
                    </SelectItem>
                  ))}
                </SelectContent>
              </Select>
            </div>
          </div>
          <DialogFooter>
            <Button variant="outline" onClick={() => setIsUpdatingRole(false)}>
              Cancel
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  );
}