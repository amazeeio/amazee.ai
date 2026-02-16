'use client';

import { useState, useMemo, Fragment } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
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
import { useToast } from '@/hooks/use-toast';
import { Loader2, ChevronUp, ChevronDown, ChevronsUpDown, ChevronRight } from 'lucide-react';
import { get, del, put } from '@/utils/api';
import { TableActionButtons } from '@/components/ui/table-action-buttons';
import { TableFilters, FilterField } from '@/components/ui/table-filters';

import { User, USER_ROLES } from '@/types/user';
import { CreateUserDialog } from './_components/create-user-dialog';
import { EditUserRoleDialog } from './_components/edit-user-role-dialog';
import { UserExpansionRow } from './_components/user-expansion-row';

type SortField = 'email' | 'team_name' | 'role' | null;
type SortDirection = 'asc' | 'desc';

export default function UsersPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingUser, setIsAddingUser] = useState(false);
  const [isUpdatingRole, setIsUpdatingRole] = useState(false);
  const [selectedUserForRole, setSelectedUserForRole] = useState<{ id: string; currentRole: string } | null>(null);
  const [expandedUserId, setExpandedUserId] = useState<string | null>(null);

  // Filter and sort state
  const [emailFilter, setEmailFilter] = useState('');
  const [teamFilter, setTeamFilter] = useState('');
  const [roleFilter, setRoleFilter] = useState('all');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  // Queries
  const { data: users = [], isLoading: isLoadingUsers } = useQuery<User[]>({
    queryKey: ['users'],
    queryFn: async () => {
      const response = await get('/users');
      return response.json();
    },
  });

  const { data: teams = [] } = useQuery<{ id: string; name: string }[]>({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('/teams');
      return response.json();
    },
  });

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
            aValue = (a.role || '').toLowerCase();
            bValue = (b.role || '').toLowerCase();
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
      return response.json();
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

  const deleteUserMutation = useMutation({
    mutationFn: async (userId: string) => {
      const response = await del(`/users/${userId}`);
      return response.json();
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
    setSelectedUserForRole({ id: userId, currentRole });
    setIsUpdatingRole(true);
  };

  const handleDeleteUser = (userId: string) => {
    deleteUserMutation.mutate(userId);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Users</h1>
        <CreateUserDialog 
          open={isAddingUser} 
          onOpenChange={setIsAddingUser} 
          teams={teams} 
        />
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
                <Fragment key={user.id}>
                  <TableRow
                    className="cursor-pointer hover:bg-muted/50"
                    onClick={() => toggleUserExpansion(user.id.toString())}
                  >
                    <TableCell>
                      {expandedUserId === user.id.toString() ? (
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
                            handleToggleAdmin(user.id.toString(), user.is_admin || false);
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
                            handleUpdateRole(user.id.toString(), user.role || '');
                          }}
                        >
                          Change Role
                        </Button>
                        <TableActionButtons
                          showEdit={false}
                          onDelete={() => handleDeleteUser(user.id.toString())}
                          deleteTitle="Are you sure?"
                          deleteDescription="This action cannot be undone. This will permanently delete the user account and all associated data."
                          isDeleting={deleteUserMutation.isPending}
                        />
                      </div>
                    </TableCell>
                  </TableRow>
                  <UserExpansionRow 
                    userId={user.id.toString()} 
                    isExpanded={expandedUserId === user.id.toString()} 
                  />
                </Fragment>
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

      <EditUserRoleDialog 
        user={selectedUserForRole} 
        open={isUpdatingRole} 
        onOpenChange={setIsUpdatingRole} 
      />
    </div>
  );
}
