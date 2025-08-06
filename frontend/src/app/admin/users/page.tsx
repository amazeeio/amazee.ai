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
import { Loader2, ChevronUp, ChevronDown, ChevronsUpDown } from 'lucide-react';
import { get, post, del, put } from '@/utils/api';
import { TableActionButtons } from '@/components/ui/table-action-buttons';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';

interface User {
  id: string;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  role: string;
  created_at: string;
  team_name?: string;
}

const USER_ROLES = [
  { value: 'admin', label: 'Admin' },
  { value: 'key_creator', label: 'Key Creator' },
  { value: 'read_only', label: 'Read Only' },
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
    let filtered = users.filter(user => {
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

  // Mutations
  const updateUserMutation = useMutation({
    mutationFn: async ({ userId, isAdmin }: { userId: string; isAdmin: boolean }) => {
      const response = await put(`/users/${userId}`, { is_admin: isAdmin });
      const data = await response.json();
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['users'] });
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
                    <label className="text-sm font-medium">Role</label>
                    <Select
                      value={newUserRole}
                      onValueChange={setNewUserRole}
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
                </>
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

      {/* Filter Controls */}
      <div className="space-y-4 p-4 bg-gray-50 rounded-md border">
        <div className="grid grid-cols-1 md:grid-cols-3 gap-4">
          <div className="space-y-2">
            <label className="text-sm font-medium">Filter by Email</label>
            <Input
              placeholder="Search by email..."
              value={emailFilter}
              onChange={(e) => setEmailFilter(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Filter by Team</label>
            <Input
              placeholder="Search by team..."
              value={teamFilter}
              onChange={(e) => setTeamFilter(e.target.value)}
            />
          </div>
          <div className="space-y-2">
            <label className="text-sm font-medium">Filter by Role</label>
            <Select value={roleFilter} onValueChange={setRoleFilter}>
              <SelectTrigger>
                <SelectValue placeholder="All roles" />
              </SelectTrigger>
              <SelectContent>
                <SelectItem value="all">All roles</SelectItem>
                {USER_ROLES.map((role) => (
                  <SelectItem key={role.value} value={role.value}>
                    {role.label}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
        </div>
        <div className="flex justify-between items-center">
          <span className="text-sm text-gray-600">
            Showing {filteredAndSortedUsers.length} of {users.length} users
          </span>
          <Button
            variant="outline"
            size="sm"
            onClick={() => {
              setEmailFilter('');
              setTeamFilter('');
              setRoleFilter('all');
              setSortField(null);
              setSortDirection('asc');
            }}
          >
            Clear Filters
          </Button>
        </div>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
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
            {filteredAndSortedUsers.map((user) => (
              <TableRow key={user.id}>
                <TableCell className="font-mono text-sm">{user.id}</TableCell>
                <TableCell>{user.email}</TableCell>
                <TableCell>
                  <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                    user.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
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
                      onClick={() => handleToggleAdmin(user.id, user.is_admin)}
                      disabled={updateUserMutation.isPending}
                    >
                      {user.is_admin ? 'Remove Admin' : 'Make Admin'}
                    </Button>
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => handleUpdateRole(user.id, 'member')}
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
            ))}
          </TableBody>
        </Table>
      </div>

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