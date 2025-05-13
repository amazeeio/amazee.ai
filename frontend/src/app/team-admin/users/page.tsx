'use client';

import { useState, useEffect } from 'react';
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
import { Loader2, UserPlus } from 'lucide-react';
import { get, post } from '@/utils/api';
import { useAuth } from '@/hooks/use-auth';
import {
  AlertDialog,
  AlertDialogAction,
  AlertDialogCancel,
  AlertDialogContent,
  AlertDialogDescription,
  AlertDialogFooter,
  AlertDialogHeader,
  AlertDialogTitle,
  AlertDialogTrigger,
} from '@/components/ui/alert-dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { getCachedConfig } from '@/utils/config';

const USER_ROLES = [
  { value: 'admin', label: 'Admin' },
  { value: 'key_creator', label: 'Key Creator' },
  { value: 'read_only', label: 'Read Only' },
];

interface TeamUser {
  id: string;
  email: string;
  is_active: boolean;
  role: string;
  team_id: number | null;
  created_at: string;
}

export default function TeamUsersPage() {
  const { toast } = useToast();
  const { user } = useAuth();
  const [isAddingUser, setIsAddingUser] = useState(false);
  const [isUpdatingRole, setIsUpdatingRole] = useState(false);
  const [selectedUser, setSelectedUser] = useState<{ id: string; currentRole: string } | null>(null);
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState('read_only');
  const [isPasswordless, setIsPasswordless] = useState(false);

  useEffect(() => {
    const config = getCachedConfig();
    setIsPasswordless(config.PASSWORDLESS_SIGN_IN);
  }, []);

  const queryClient = useQueryClient();

  const { data: users = [], isLoading: isLoadingUsers } = useQuery<TeamUser[]>({
    queryKey: ['team-users', user?.team_id],
    queryFn: async () => {
      const response = await get('users', { credentials: 'include' });
      const allUsers = await response.json();
      // Filter users to only show those in the current team
      return allUsers.filter((u: TeamUser) => u.team_id === user?.team_id);
    },
    enabled: !!user?.team_id,
  });

  const createUserMutation = useMutation({
    mutationFn: async (data: { email: string; password?: string; role: string }) => {
      const response = await post('users', {
        ...data,
        team_id: user?.team_id,
      }, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-users'] });
      setIsAddingUser(false);
      setNewUserEmail('');
      setNewUserPassword('');
      setNewUserRole('read_only');
      toast({
        title: 'Success',
        description: 'User added to team successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to add user to team',
        variant: 'destructive',
      });
    },
  });

  const updateUserRoleMutation = useMutation({
    mutationFn: async ({ userId, role }: { userId: string; role: string }) => {
      const response = await post(`users/${userId}/role`, { role }, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-users'] });
      toast({
        title: 'Success',
        description: 'User role updated successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to update user role',
        variant: 'destructive',
      });
    },
  });

  const removeUserMutation = useMutation({
    mutationFn: async (userId: string) => {
      const response = await post(`users/${userId}/remove-from-team`, {}, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team-users'] });
      toast({
        title: 'Success',
        description: 'User removed from team successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to remove user from team',
        variant: 'destructive',
      });
    },
  });

  if (isLoadingUsers) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

  const handleCreateUser = (e: React.FormEvent) => {
    e.preventDefault();
    createUserMutation.mutate({
      email: newUserEmail,
      password: isPasswordless ? undefined : newUserPassword,
      role: newUserRole,
    });
  };

  const handleUpdateRole = (userId: string, currentRole: string) => {
    setSelectedUser({ id: userId, currentRole });
    setIsUpdatingRole(true);
  };

  const handleRemoveUser = (userId: string) => {
    removeUserMutation.mutate(userId);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Team Users</h1>
        <Dialog open={isAddingUser} onOpenChange={setIsAddingUser}>
          <DialogTrigger asChild>
            <Button>
              <UserPlus className="mr-2 h-4 w-4" />
              Add User
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add User to Team</DialogTitle>
              <DialogDescription>
                Add a new user to your team.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateUser}>
              <div className="grid gap-4 py-4">
                <div className="grid gap-2">
                  <label htmlFor="email">Email</label>
                  <Input
                    id="email"
                    type="email"
                    value={newUserEmail}
                    onChange={(e) => setNewUserEmail(e.target.value)}
                    required
                  />
                </div>
                {!isPasswordless && (
                  <div className="grid gap-2">
                    <label htmlFor="password">Password</label>
                    <Input
                      id="password"
                      type="password"
                      value={newUserPassword}
                      onChange={(e) => setNewUserPassword(e.target.value)}
                      required
                    />
                  </div>
                )}
                <div className="grid gap-2">
                  <label htmlFor="role">Role</label>
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
              </div>
              <DialogFooter>
                <Button type="submit" disabled={createUserMutation.isPending}>
                  {createUserMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Add User
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Email</TableHead>
              <TableHead>Status</TableHead>
              <TableHead>Team Role</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {users.map((user) => (
              <TableRow key={user.id}>
                <TableCell>{user.email}</TableCell>
                <TableCell>
                  <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                    user.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                  }`}>
                    {user.is_active ? 'Active' : 'Inactive'}
                  </span>
                </TableCell>
                <TableCell>
                  {USER_ROLES.find(r => r.value === user.role)?.label || user.role}
                </TableCell>
                <TableCell>
                  <div className="flex gap-2">
                    <Button
                      variant="secondary"
                      size="sm"
                      onClick={() => handleUpdateRole(user.id, user.role)}
                      disabled={updateUserRoleMutation.isPending}
                    >
                      Change Role
                    </Button>
                    <AlertDialog>
                      <AlertDialogTrigger asChild>
                        <Button variant="destructive" size="sm">
                          Remove
                        </Button>
                      </AlertDialogTrigger>
                      <AlertDialogContent>
                        <AlertDialogHeader>
                          <AlertDialogTitle>Are you sure?</AlertDialogTitle>
                          <AlertDialogDescription>
                            This will remove the user from your team. They will lose access to all team resources.
                          </AlertDialogDescription>
                        </AlertDialogHeader>
                        <AlertDialogFooter>
                          <AlertDialogCancel>Cancel</AlertDialogCancel>
                          <AlertDialogAction onClick={() => handleRemoveUser(user.id)}>
                            Remove
                          </AlertDialogAction>
                        </AlertDialogFooter>
                      </AlertDialogContent>
                    </AlertDialog>
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
              Select a new role for this user. This will change their permissions within the team.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-4 py-4">
            <div className="space-y-2">
              <label className="text-sm font-medium">Role</label>
              <Select
                value={selectedUser?.currentRole || 'read_only'}
                onValueChange={(value) => {
                  updateUserRoleMutation.mutate({
                    userId: selectedUser!.id,
                    role: value,
                  });
                  setIsUpdatingRole(false);
                  setSelectedUser(null);
                }}
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