'use client';

import { useState, useEffect, useMemo } from 'react';
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
import { Loader2, Plus, ChevronDown, ChevronRight, UserPlus, ChevronUp, ChevronsUpDown } from 'lucide-react';
import { get, post, del } from '@/utils/api';
import {
  Collapsible,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DeleteConfirmationDialog } from '@/components/ui/delete-confirmation-dialog';
import { Alert, AlertDescription } from '@/components/ui/alert';

import { TableActionButtons } from '@/components/ui/table-action-buttons';
import { TableFilters, FilterField } from '@/components/ui/table-filters';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import React from 'react';
import { getCachedConfig } from '@/utils/config';

const USER_ROLES = [
  { value: 'admin', label: 'Admin' },
  { value: 'key_creator', label: 'Key Creator' },
  { value: 'read_only', label: 'Read Only' },
];

interface TeamUser {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
  role: string;
}

interface Product {
  id: string;
  name: string;
  user_count: number;
  keys_per_user: number;
  total_key_count: number;
  service_key_count: number;
  max_budget_per_key: number;
  rpm_per_key: number;
  vector_db_count: number;
  vector_db_storage: number;
  renewal_period_days: number;
  active: boolean;
  created_at: string;
}

interface Team {
  id: string;
  name: string;
  admin_email: string;
  phone: string;
  billing_address: string;
  is_active: boolean;
  created_at: string;
  updated_at: string;
  last_payment?: string;
  users?: TeamUser[];
  products?: Product[];
  is_always_free: boolean;
}

interface User {
  id: number;
  email: string;
  is_active: boolean;
  is_admin: boolean;
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

type SortField = 'name' | 'admin_email' | 'is_active' | 'created_at' | null;
type SortDirection = 'asc' | 'desc';

export default function TeamsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingTeam, setIsAddingTeam] = useState(false);
  const [newTeamName, setNewTeamName] = useState('');
  const [newTeamAdminEmail, setNewTeamAdminEmail] = useState('');
  const [expandedTeamId, setExpandedTeamId] = useState<string | null>(null);
  const [isAddingUserToTeam, setIsAddingUserToTeam] = useState(false);
  const [isCreatingUserInTeam, setIsCreatingUserInTeam] = useState(false);
  const [selectedTeamId, setSelectedTeamId] = useState<string>('');
  const [newUserEmail, setNewUserEmail] = useState('');
  const [newUserPassword, setNewUserPassword] = useState('');
  const [newUserRole, setNewUserRole] = useState('read_only');
  const [searchQuery, setSearchQuery] = useState('');
  const [searchResults, setSearchResults] = useState<User[]>([]);
  const [isSearching, setIsSearching] = useState(false);
  const [isPasswordless, setIsPasswordless] = useState(false);
  const [isSubscribingToProduct, setIsSubscribingToProduct] = useState(false);
  const [selectedProductId, setSelectedProductId] = useState('');
  const [isMergingTeams, setIsMergingTeams] = useState(false);
  const [selectedSourceTeamId, setSelectedSourceTeamId] = useState('');
  const [conflictResolutionStrategy, setConflictResolutionStrategy] = useState<'delete' | 'rename' | 'cancel'>('rename');
  const [renameSuffix, setRenameSuffix] = useState('_merged');

  // Filter and sort state
  const [nameFilter, setNameFilter] = useState('');
  const [adminEmailFilter, setAdminEmailFilter] = useState('');
  const [statusFilter, setStatusFilter] = useState('all');
  const [sortField, setSortField] = useState<SortField>(null);
  const [sortDirection, setSortDirection] = useState<SortDirection>('asc');

  useEffect(() => {
    const config = getCachedConfig();
    setIsPasswordless(config.PASSWORDLESS_SIGN_IN);
  }, []);

  // Helper function to determine if a team is expired (and has no active products)
  const isTeamExpired = (team: Team): boolean => {
    // Teams with active products are never considered expired
    if (team.products && team.products.some(product => product.active)) {
      return false;
    }

    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);

    const createdAt = new Date(team.created_at);

    // If team was created less than 30 days ago, it's not expired
    if (createdAt >= thirtyDaysAgo) {
      return false;
    }

    // If team has no last_payment, it's expired if created more than 30 days ago
    if (!team.last_payment) {
      return true;
    }

    const lastPayment = new Date(team.last_payment);

    // Team is expired if last_payment is more than 30 days ago
    return lastPayment < thirtyDaysAgo;
  };

  // Queries
  const { data: teams = [], isLoading: isLoadingTeams } = useQuery<Team[]>({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('/teams');
      const data = await response.json();
      return data;
    },
    staleTime: 0, // Always consider data stale to ensure immediate refetch
    refetchOnWindowFocus: false, // Prevent unnecessary refetches
  });

  // Filtered and sorted teams
  const filteredAndSortedTeams = useMemo(() => {
    const filtered = teams.filter(team => {
      const nameMatch = team.name.toLowerCase().includes(nameFilter.toLowerCase());
      const adminEmailMatch = team.admin_email.toLowerCase().includes(adminEmailFilter.toLowerCase());
      const statusMatch = statusFilter === 'all' ||
        (statusFilter === 'active' && team.is_active) ||
        (statusFilter === 'inactive' && !team.is_active);

      return nameMatch && adminEmailMatch && statusMatch;
    });

    if (sortField) {
      filtered.sort((a, b) => {
        let aValue: string | boolean | Date;
        let bValue: string | boolean | Date;

        switch (sortField) {
          case 'name':
            aValue = a.name.toLowerCase();
            bValue = b.name.toLowerCase();
            break;
          case 'admin_email':
            aValue = a.admin_email.toLowerCase();
            bValue = b.admin_email.toLowerCase();
            break;
          case 'is_active':
            aValue = a.is_active;
            bValue = b.is_active;
            break;
          case 'created_at':
            aValue = new Date(a.created_at);
            bValue = new Date(b.created_at);
            break;
          default:
            return 0;
        }

        if (typeof aValue === 'string' && typeof bValue === 'string') {
          if (sortDirection === 'asc') {
            return aValue.localeCompare(bValue);
          } else {
            return bValue.localeCompare(aValue);
          }
        } else if (typeof aValue === 'boolean' && typeof bValue === 'boolean') {
          if (sortDirection === 'asc') {
            return aValue === bValue ? 0 : aValue ? -1 : 1;
          } else {
            return aValue === bValue ? 0 : aValue ? 1 : -1;
          }
        } else if (aValue instanceof Date && bValue instanceof Date) {
          if (sortDirection === 'asc') {
            return aValue.getTime() - bValue.getTime();
          } else {
            return bValue.getTime() - aValue.getTime();
          }
        }

        return 0;
      });
    }

    return filtered;
  }, [teams, nameFilter, adminEmailFilter, statusFilter, sortField, sortDirection]);

  const hasActiveFilters = Boolean(nameFilter.trim() || adminEmailFilter.trim() || statusFilter !== 'all');

  // Filter fields configuration
  const filterFields: FilterField[] = [
    {
      key: 'name',
      label: 'Filter by Name',
      type: 'search',
      placeholder: 'Search by team name...',
      value: nameFilter,
      onChange: setNameFilter,
    },
    {
      key: 'adminEmail',
      label: 'Filter by Admin Email',
      type: 'search',
      placeholder: 'Search by admin email...',
      value: adminEmailFilter,
      onChange: setAdminEmailFilter,
    },
    {
      key: 'status',
      label: 'Filter by Status',
      type: 'select',
      placeholder: 'All statuses',
      value: statusFilter,
      onChange: setStatusFilter,
      options: [
        { value: 'all', label: 'All statuses' },
        { value: 'active', label: 'Active' },
        { value: 'inactive', label: 'Inactive' },
      ],
    },
  ];

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

  // Get team details when expanded
  const { data: expandedTeam, isLoading: isLoadingTeamDetails } = useQuery<Team>({
    queryKey: ['team', expandedTeamId],
    queryFn: async () => {
      if (!expandedTeamId) return null;
      const response = await get(`/teams/${expandedTeamId}`);
      return response.json();
    },
    enabled: !!expandedTeamId,
  });

  // Get products for expanded team
  const { data: teamProducts = [], isLoading: isLoadingTeamProducts } = useQuery<Product[]>({
    queryKey: ['team-products', expandedTeamId],
    queryFn: async () => {
      if (!expandedTeamId) return [];
      const response = await get(`/products?team_id=${expandedTeamId}`);
      return response.json();
    },
    enabled: !!expandedTeamId,
  });

  // Get all products
  const { data: allProducts = [], isLoading: isLoadingAllProducts } = useQuery<Product[]>({
    queryKey: ['products'],
    queryFn: async () => {
      const response = await get('/products');
      return response.json();
    },
  });

  // Get team AI keys when expanded
  const { data: teamAIKeys = [], isLoading: isLoadingTeamAIKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['team-ai-keys', expandedTeamId],
    queryFn: async () => {
      if (!expandedTeamId) return [];
      const response = await get(`/private-ai-keys?team_id=${expandedTeamId}`);
      return response.json();
    },
    enabled: !!expandedTeamId,
  });

  // Get users map for displaying owner emails
  const { data: usersMap = {} } = useQuery<Record<number, User>>({
    queryKey: ['users-map'],
    queryFn: async () => {
      const response = await get('/users');
      const users: User[] = await response.json();
      return users.reduce((acc, user) => ({
        ...acc,
        [user.id]: user
      }), {});
    },
  });

  // Get spend data for each key
  const { data: spendMap = {} } = useQuery<Record<number, SpendInfo>>({
    queryKey: ['team-ai-keys-spend', expandedTeamId, teamAIKeys],
    queryFn: async () => {
      if (!expandedTeamId || teamAIKeys.length === 0) return {};

      const spendData: Record<number, SpendInfo> = {};

      for (const key of teamAIKeys) {
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
    enabled: !!expandedTeamId && teamAIKeys.length > 0,
  });

  // Search users query
  const searchUsersMutation = useMutation({
    mutationFn: async (query: string) => {
      if (!query) return [];
      const response = await get(`/users/search?email=${encodeURIComponent(query)}`);
      return response.json();
    },
    onSuccess: (data) => {
      setSearchResults(data);
      setIsSearching(false);
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
      setIsSearching(false);
    },
  });

  // Mutations
  const createTeamMutation = useMutation({
    mutationFn: async (teamData: {
      name: string;
      admin_email: string;
    }) => {
      try {
        const response = await post('/teams', teamData);
        return response.json();
      } catch (error) {
        // Handle different types of errors
        if (error instanceof Error) {
          // If it's a network error or other error with a message
          throw new Error(`Failed to create team: ${error.message}`);
        } else if (typeof error === 'object' && error !== null && 'status' in error) {
          // If it's a response error with status
          const status = (error as { status: number }).status;
          if (status === 500) {
            throw new Error('Server error: Failed to create team. Please try again later.');
          } else if (status === 400) {
            throw new Error('Invalid team data. Please check your inputs and try again.');
          } else if (status === 409) {
            throw new Error('A team with this email already exists.');
          } else {
            throw new Error(`Failed to create team (Status: ${status})`);
          }
        } else {
          // Generic error
          throw new Error('An unexpected error occurred while creating the team.');
        }
      }
    },
    onSuccess: () => {
      // Invalidate and refetch the teams query to reload the list
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      // Force a refetch to ensure we have the latest data
      queryClient.refetchQueries({ queryKey: ['teams'], exact: true });

      toast({
        title: 'Success',
        description: 'Team created successfully',
      });
      setIsAddingTeam(false);
      setNewTeamName('');
      setNewTeamAdminEmail('');
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
    mutationFn: async (userData: { email: string; password?: string; team_id?: number; role: string }) => {
      try {
        const response = await post('/users', userData);
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to create user: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while creating the user.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'User created successfully',
      });
      setIsCreatingUserInTeam(false);
      setNewUserEmail('');
      setNewUserPassword('');
      setNewUserRole('read_only');
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const addUserToTeamMutation = useMutation({
    mutationFn: async ({ userId, teamId }: { userId: number; teamId: string }) => {
      try {
        const response = await post(`/users/${userId}/add-to-team`, { team_id: parseInt(teamId) });
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to add user to team: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while adding the user to the team.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'User added to team successfully',
      });
      setIsAddingUserToTeam(false);
      setSearchQuery('');
      setSearchResults([]);
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const removeUserFromTeamMutation = useMutation({
    mutationFn: async (userId: number) => {
      try {
        const response = await post(`/users/${userId}/remove-from-team`, {});
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to remove user from team: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while removing the user from the team.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'User removed from team successfully',
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

  const extendTrialMutation = useMutation({
    mutationFn: async (teamId: string) => {
      try {
        const response = await post(`/teams/${teamId}/extend-trial`, {});
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to extend trial: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while extending the trial.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });

      toast({
        title: 'Success',
        description: 'Trial extended successfully',
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

  const deleteTeamMutation = useMutation({
    mutationFn: async (teamId: string) => {
      try {
        const response = await del(`/teams/${teamId}`);
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to delete team: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while deleting the team.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      setExpandedTeamId(null);

      toast({
        title: 'Success',
        description: 'Team deleted successfully',
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



  const createTeamSubscriptionMutation = useMutation({
    mutationFn: async ({ teamId, productId }: { teamId: string; productId: string }) => {
      try {
        const response = await post(`/billing/teams/${teamId}/subscriptions`, { product_id: productId });
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to create subscription: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while creating the subscription.');
        }
      }
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team-products', selectedTeamId] });

      toast({
        title: 'Success',
        description: 'Team subscribed to product successfully',
      });
      setIsSubscribingToProduct(false);
      setSelectedProductId('');
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const mergeTeamsMutation = useMutation({
    mutationFn: async ({ targetTeamId, sourceTeamId, conflictResolutionStrategy, renameSuffix }: {
      targetTeamId: string;
      sourceTeamId: string;
      conflictResolutionStrategy: 'delete' | 'rename' | 'cancel';
      renameSuffix?: string;
    }) => {
      try {
        // Find team IDs by name
        const targetTeam = teams.find(team => team.name === targetTeamId);
        const sourceTeam = teams.find(team => team.name === sourceTeamId);

        if (!targetTeam || !sourceTeam) {
          throw new Error('Selected team not found');
        }

        const payload: {
          source_team_id: number;
          conflict_resolution_strategy: 'delete' | 'rename' | 'cancel';
          rename_suffix?: string;
        } = {
          source_team_id: parseInt(sourceTeam.id),
          conflict_resolution_strategy: conflictResolutionStrategy,
        };

        if (conflictResolutionStrategy === 'rename' && renameSuffix) {
          payload.rename_suffix = renameSuffix;
        }

        const response = await post(`/teams/${targetTeam.id}/merge`, payload);
        return response.json();
      } catch (error) {
        if (error instanceof Error) {
          throw new Error(`Failed to merge teams: ${error.message}`);
        } else {
          throw new Error('An unexpected error occurred while merging the teams.');
        }
      }
    },
    onSuccess: (data) => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team', selectedTeamId] });
      setExpandedTeamId(null);

      toast({
        title: 'Success',
        description: data.message || 'Teams merged successfully',
      });
      setIsMergingTeams(false);
      setSelectedSourceTeamId('');
      setConflictResolutionStrategy('rename');
      setRenameSuffix('_merged');
    },
    onError: (error: Error) => {
      toast({
        title: 'Error',
        description: error.message,
        variant: 'destructive',
      });
    },
  });

  const handleCreateTeam = (e: React.FormEvent) => {
    e.preventDefault();
    createTeamMutation.mutate({
      name: newTeamName,
      admin_email: newTeamAdminEmail,
    });
  };

  const handleCreateUserInTeam = (e: React.FormEvent) => {
    e.preventDefault();
    const userData: {
      email: string;
      password?: string;
      team_id?: number;
      role: string;
    } = {
      email: newUserEmail,
      role: newUserRole,
    };

    if (!isPasswordless) {
      userData.password = newUserPassword;
    }

    if (selectedTeamId) {
      userData.team_id = parseInt(selectedTeamId);
    }

    createUserMutation.mutate(userData);
  };

  const handleAddUserToTeam = (userId: number, e: React.MouseEvent) => {
    e.stopPropagation();
    if (!selectedTeamId) return;

    addUserToTeamMutation.mutate({
      userId,
      teamId: selectedTeamId,
    });
  };



  const handleSearchUsers = (e: React.FormEvent) => {
    e.preventDefault();
    if (!searchQuery.trim()) return;

    setIsSearching(true);
    searchUsersMutation.mutate(searchQuery);
  };

  const toggleTeamExpansion = (teamId: string) => {
    if (expandedTeamId === teamId) {
      setExpandedTeamId(null);
    } else {
      setExpandedTeamId(teamId);
    }
  };

  const openAddUserToTeamDialog = (teamId: string) => {
    setSelectedTeamId(teamId);
    setIsAddingUserToTeam(true);
  };

  const openCreateUserInTeamDialog = (teamId: string) => {
    setSelectedTeamId(teamId);
    setIsCreatingUserInTeam(true);
  };

  const openSubscribeToProductDialog = (teamId: string) => {
    setSelectedTeamId(teamId);
    setIsSubscribingToProduct(true);
  };

  const handleCreateTeamSubscription = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTeamId || !selectedProductId) return;

    createTeamSubscriptionMutation.mutate({
      teamId: selectedTeamId,
      productId: selectedProductId,
    });
  };

  const handleMergeTeams = (e: React.FormEvent) => {
    e.preventDefault();
    if (!selectedTeamId || !selectedSourceTeamId) return;

    mergeTeamsMutation.mutate({
      targetTeamId: selectedTeamId,
      sourceTeamId: selectedSourceTeamId,
      conflictResolutionStrategy,
      renameSuffix: conflictResolutionStrategy === 'rename' ? renameSuffix : undefined,
    });
  };



  return (
    <div className="container mx-auto py-10">
      <div className="space-y-4">
        <div className="flex justify-between items-center">
          <h1 className="text-3xl font-bold">Teams</h1>
          <div className="flex space-x-2">
            <Dialog open={isMergingTeams} onOpenChange={setIsMergingTeams}>
              <DialogTrigger asChild>
                <Button variant="outline">
                  Merge Teams
                </Button>
              </DialogTrigger>
            </Dialog>
            <Dialog open={isAddingTeam} onOpenChange={setIsAddingTeam}>
              <DialogTrigger asChild>
                <Button>
                  <Plus className="mr-2 h-4 w-4" />
                  Add Team
                </Button>
              </DialogTrigger>
            </Dialog>
          </div>
        </div>

        <TableFilters
          filters={filterFields}
          onClearFilters={() => {
            setNameFilter('');
            setAdminEmailFilter('');
            setStatusFilter('all');
            setSortField(null);
            setSortDirection('asc');
          }}
          hasActiveFilters={hasActiveFilters}
          totalItems={teams.length}
          filteredItems={filteredAndSortedTeams.length}
        />

        {isLoadingTeams ? (
          <div className="flex justify-center items-center h-64">
            <Loader2 className="h-8 w-8 animate-spin" />
          </div>
        ) : (
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead className="w-10"></TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('name')}
                  >
                    <div className="flex items-center gap-2">
                      Name
                      {getSortIcon('name')}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('admin_email')}
                  >
                    <div className="flex items-center gap-2">
                      Admin Email
                      {getSortIcon('admin_email')}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('is_active')}
                  >
                    <div className="flex items-center gap-2">
                      Status
                      {getSortIcon('is_active')}
                    </div>
                  </TableHead>
                  <TableHead
                    className="cursor-pointer hover:bg-gray-50"
                    onClick={() => handleSort('created_at')}
                  >
                    <div className="flex items-center gap-2">
                      Created At
                      {getSortIcon('created_at')}
                    </div>
                  </TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {filteredAndSortedTeams.length === 0 ? (
                  <TableRow>
                    <TableCell colSpan={7} className="text-center py-6">
                      No teams found. Create a new team to get started.
                    </TableCell>
                  </TableRow>
                ) : (
                  filteredAndSortedTeams.map((team) => (
                    <React.Fragment key={team.id}>
                      <TableRow
                        className="cursor-pointer hover:bg-muted/50"
                        onClick={() => toggleTeamExpansion(team.id)}
                      >
                        <TableCell>
                          {expandedTeamId === team.id ? (
                            <ChevronDown className="h-4 w-4" />
                          ) : (
                            <ChevronRight className="h-4 w-4" />
                          )}
                        </TableCell>
                        <TableCell className="font-medium">{team.name}</TableCell>
                        <TableCell>{team.admin_email}</TableCell>
                        <TableCell>
                          <div className="flex flex-col gap-1">
                            <span
                              className={`inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium ${team.is_active
                                ? 'bg-green-100 text-green-800'
                                : 'bg-red-100 text-red-800'
                                }`}
                            >
                              {team.is_active ? 'Active' : 'Inactive'}
                            </span>
                            {isTeamExpired(team) && (
                              <span className="inline-flex items-center rounded-full px-2.5 py-0.5 text-xs font-medium bg-red-600 text-white">
                                Expired
                              </span>
                            )}
                          </div>
                        </TableCell>
                        <TableCell>
                          {new Date(team.created_at).toLocaleDateString()}
                        </TableCell>
                      </TableRow>
                      {expandedTeamId === team.id && (
                        <TableRow>
                          <TableCell colSpan={7} className="p-0">
                            <Collapsible open={expandedTeamId === team.id}>
                              <CollapsibleContent className="p-4 bg-muted/30">
                                {isLoadingTeamDetails ? (
                                  <div className="flex justify-center items-center py-8">
                                    <Loader2 className="h-8 w-8 animate-spin" />
                                  </div>
                                ) : expandedTeam ? (
                                  <div className="space-y-6">
                                    <Tabs defaultValue="details">
                                      <TabsList>
                                        <TabsTrigger value="details">Team Details</TabsTrigger>
                                        <TabsTrigger value="users">Users</TabsTrigger>
                                        <TabsTrigger value="products">Products</TabsTrigger>
                                        <TabsTrigger value="shared-keys">Shared Keys</TabsTrigger>
                                      </TabsList>
                                      <TabsContent value="details" className="mt-4">
                                        <Card>
                                          <CardHeader>
                                            <CardTitle>Team Information</CardTitle>
                                            <CardDescription>
                                              Detailed information about the team
                                            </CardDescription>
                                          </CardHeader>
                                          <CardContent className="space-y-4">
                                            <div className="grid grid-cols-2 gap-4">
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Name</p>
                                                <p>{expandedTeam.name}</p>
                                              </div>
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Admin Email</p>
                                                <p>{expandedTeam.admin_email}</p>
                                              </div>
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Phone</p>
                                                <p>{expandedTeam.phone}</p>
                                              </div>
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Billing Address</p>
                                                <p>{expandedTeam.billing_address}</p>
                                              </div>
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Status</p>
                                                <Badge variant={expandedTeam.is_active ? "default" : "destructive"}>
                                                  {expandedTeam.is_active ? "Active" : "Inactive"}
                                                </Badge>
                                              </div>
                                              {isTeamExpired(expandedTeam) && (
                                                <div>
                                                  <p className="text-sm font-medium text-muted-foreground">Expiration Status</p>
                                                  <Badge variant="destructive" className="bg-red-600 hover:bg-red-700">
                                                    Expired
                                                  </Badge>
                                                </div>
                                              )}
                                              {expandedTeam.is_always_free && (
                                                <div>
                                                  <p className="text-sm font-medium text-muted-foreground">Always Free Status</p>
                                                  <Badge variant="default" className="bg-green-500 hover:bg-green-600">
                                                    Always Free
                                                  </Badge>
                                                </div>
                                              )}
                                              <div>
                                                <p className="text-sm font-medium text-muted-foreground">Created At</p>
                                                <p>{new Date(expandedTeam.created_at).toLocaleString()}</p>
                                              </div>
                                              {expandedTeam.updated_at && (
                                                <div>
                                                  <p className="text-sm font-medium text-muted-foreground">Updated At</p>
                                                  <p>{new Date(expandedTeam.updated_at).toLocaleString()}</p>
                                                </div>
                                              )}
                                              {expandedTeam.last_payment && (
                                                <div>
                                                  <p className="text-sm font-medium text-muted-foreground">Last Payment</p>
                                                  <p>{new Date(expandedTeam.last_payment).toLocaleString()}</p>
                                                </div>
                                              )}
                                            </div>
                                            <div className="flex justify-end space-x-2 mt-4">
                                              <Button
                                                variant="outline"
                                                onClick={() => openSubscribeToProductDialog(expandedTeam.id)}
                                              >
                                                Subscribe to Product
                                              </Button>
                                              <Button
                                                variant="outline"
                                                onClick={() => extendTrialMutation.mutate(expandedTeam.id)}
                                                disabled={extendTrialMutation.isPending}
                                              >
                                                {extendTrialMutation.isPending ? (
                                                  <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                                ) : null}
                                                Extend Trial
                                              </Button>

                                              {(!expandedTeam.users || expandedTeam.users.length === 0) && (
                                                <DeleteConfirmationDialog
                                                  title="Delete Team"
                                                  description="Are you sure you want to delete this team? This action cannot be undone."
                                                  triggerText="Delete Team"
                                                  onConfirm={() => deleteTeamMutation.mutate(expandedTeam.id)}
                                                  isLoading={deleteTeamMutation.isPending}
                                                  size="default"
                                                />
                                              )}
                                            </div>
                                          </CardContent>
                                        </Card>
                                      </TabsContent>
                                      <TabsContent value="users" className="mt-4">
                                        <div className="flex justify-between items-center mb-4">
                                          <h3 className="text-lg font-medium">Team Users</h3>
                                          <div className="space-x-2">
                                            <Button
                                              size="sm"
                                              onClick={() => openAddUserToTeamDialog(team.id)}
                                            >
                                              <UserPlus className="mr-2 h-4 w-4" />
                                              Add Existing User
                                            </Button>
                                            <Button
                                              size="sm"
                                              onClick={() => openCreateUserInTeamDialog(team.id)}
                                            >
                                              <Plus className="mr-2 h-4 w-4" />
                                              Create New User
                                            </Button>
                                          </div>
                                        </div>

                                        {expandedTeam.users && expandedTeam.users.length > 0 ? (
                                          <div className="rounded-md border">
                                            <Table>
                                              <TableHeader>
                                                <TableRow>
                                                  <TableHead>Email</TableHead>
                                                  <TableHead>Role</TableHead>
                                                  <TableHead>Status</TableHead>
                                                  <TableHead>Admin</TableHead>
                                                  <TableHead className="text-right">Actions</TableHead>
                                                </TableRow>
                                              </TableHeader>
                                              <TableBody>
                                                {expandedTeam.users.map((user) => (
                                                  <TableRow key={user.id}>
                                                    <TableCell>{user.email}</TableCell>
                                                    <TableCell>{user.role || 'User'}</TableCell>
                                                    <TableCell>
                                                      <Badge variant={user.is_active ? "default" : "destructive"}>
                                                        {user.is_active ? "Active" : "Inactive"}
                                                      </Badge>
                                                    </TableCell>
                                                    <TableCell>
                                                      <Badge variant={user.is_admin ? "default" : "outline"}>
                                                        {user.is_admin ? "Yes" : "No"}
                                                      </Badge>
                                                    </TableCell>
                                                    <TableCell className="text-right">
                                                      <TableActionButtons
                                                        showEdit={false}
                                                        onDelete={() => removeUserFromTeamMutation.mutate(user.id)}
                                                        deleteTitle="Remove User"
                                                        deleteDescription="Are you sure you want to remove this user from the team?"
                                                        deleteText="Remove"
                                                        deleteConfirmText="Remove"
                                                        isDeleting={removeUserFromTeamMutation.isPending}
                                                        className="justify-end"
                                                      />
                                                    </TableCell>
                                                  </TableRow>
                                                ))}
                                              </TableBody>
                                            </Table>
                                          </div>
                                        ) : (
                                          <div className="text-center py-8 border rounded-md">
                                            <p className="text-muted-foreground">No users in this team yet.</p>
                                            <p className="text-sm text-muted-foreground mt-2">
                                              Add existing users or create new ones to get started.
                                            </p>
                                          </div>
                                        )}
                                      </TabsContent>
                                      <TabsContent value="products" className="mt-4">
                                        <div className="space-y-4">
                                          {isLoadingTeamProducts ? (
                                            <div className="flex justify-center items-center py-8">
                                              <Loader2 className="h-8 w-8 animate-spin" />
                                            </div>
                                          ) : teamProducts.length > 0 ? (
                                            <div className="rounded-md border">
                                              <Table>
                                                <TableHeader>
                                                  <TableRow>
                                                    <TableHead>Name</TableHead>
                                                    <TableHead>User Count</TableHead>
                                                    <TableHead>Keys/User</TableHead>
                                                    <TableHead>Total Keys</TableHead>
                                                    <TableHead>Service Keys</TableHead>
                                                    <TableHead>Budget/Key</TableHead>
                                                    <TableHead>RPM/Key</TableHead>
                                                    <TableHead>Vector DBs</TableHead>
                                                    <TableHead>Storage (GiB)</TableHead>
                                                    <TableHead>Renewal (Days)</TableHead>
                                                    <TableHead>Status</TableHead>
                                                  </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                  {teamProducts.map((product) => (
                                                    <TableRow key={product.id}>
                                                      <TableCell>{product.name}</TableCell>
                                                      <TableCell>{product.user_count}</TableCell>
                                                      <TableCell>{product.keys_per_user}</TableCell>
                                                      <TableCell>{product.total_key_count}</TableCell>
                                                      <TableCell>{product.service_key_count}</TableCell>
                                                      <TableCell>${product.max_budget_per_key.toFixed(2)}</TableCell>
                                                      <TableCell>{product.rpm_per_key}</TableCell>
                                                      <TableCell>{product.vector_db_count}</TableCell>
                                                      <TableCell>{product.vector_db_storage}</TableCell>
                                                      <TableCell>{product.renewal_period_days}</TableCell>
                                                      <TableCell>
                                                        <Badge variant={product.active ? "default" : "destructive"}>
                                                          {product.active ? "Active" : "Inactive"}
                                                        </Badge>
                                                      </TableCell>
                                                    </TableRow>
                                                  ))}
                                                </TableBody>
                                              </Table>
                                            </div>
                                          ) : (
                                            <div className="text-center py-8 border rounded-md">
                                              <p className="text-muted-foreground">No products associated with this team.</p>
                                            </div>
                                          )}
                                        </div>
                                      </TabsContent>
                                      <TabsContent value="shared-keys" className="mt-4">
                                        <div className="space-y-4">
                                          {isLoadingTeamAIKeys ? (
                                            <div className="flex justify-center items-center py-8">
                                              <Loader2 className="h-8 w-8 animate-spin" />
                                            </div>
                                          ) : teamAIKeys.length > 0 ? (
                                            <div className="rounded-md border">
                                              <Table>
                                                <TableHeader>
                                                  <TableRow>
                                                    <TableHead>Name</TableHead>
                                                    <TableHead>Owner</TableHead>
                                                    <TableHead>Region</TableHead>
                                                    <TableHead>Database</TableHead>
                                                    <TableHead>Created At</TableHead>
                                                    <TableHead>Spend</TableHead>
                                                    <TableHead>Budget</TableHead>
                                                  </TableRow>
                                                </TableHeader>
                                                <TableBody>
                                                  {teamAIKeys.map((key) => {
                                                    const spendInfo = spendMap[key.id];
                                                    const owner = usersMap[key.owner_id];
                                                    return (
                                                      <TableRow key={key.id}>
                                                        <TableCell>{key.name}</TableCell>
                                                        <TableCell>
                                                          {key.owner_id ? (
                                                            owner ? owner.email : `User ${key.owner_id}`
                                                          ) : key.team_id ? (
                                                            <span>(Team) {expandedTeam?.name || 'Team (Shared)'}</span>
                                                          ) : (
                                                            <span className="text-muted-foreground">Unknown</span>
                                                          )}
                                                        </TableCell>
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
                                              <p className="text-muted-foreground">No shared AI keys found for this team.</p>
                                            </div>
                                          )}
                                        </div>
                                      </TabsContent>
                                    </Tabs>
                                  </div>
                                ) : (
                                  <div className="text-center py-8">
                                    <p className="text-muted-foreground">Failed to load team details.</p>
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
        )}

        {/* Dialog for adding existing users to a team */}
        <Dialog open={isAddingUserToTeam} onOpenChange={setIsAddingUserToTeam}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add User to Team</DialogTitle>
              <DialogDescription>
                Search for an existing user to add to this team.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleSearchUsers} className="space-y-4">
              <div className="flex space-x-2">
                <Input
                  placeholder="Search by email..."
                  value={searchQuery}
                  onChange={(e) => setSearchQuery(e.target.value)}
                  className="flex-1"
                />
                <Button type="submit" disabled={isSearching}>
                  {isSearching ? (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  ) : (
                    "Search"
                  )}
                </Button>
              </div>
            </form>

            {searchResults.length > 0 && (
              <div className="mt-4">
                <h4 className="text-sm font-medium mb-2">Search Results</h4>
                <div className="rounded-md border">
                  <Table>
                    <TableHeader>
                      <TableRow>
                        <TableHead>Email</TableHead>
                        <TableHead>Status</TableHead>
                        <TableHead>Admin</TableHead>
                        <TableHead className="w-20"></TableHead>
                      </TableRow>
                    </TableHeader>
                    <TableBody>
                      {searchResults.map((user) => (
                        <TableRow key={user.id}>
                          <TableCell>{user.email}</TableCell>
                          <TableCell>
                            <Badge variant={user.is_active ? "default" : "destructive"}>
                              {user.is_active ? "Active" : "Inactive"}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Badge variant={user.is_admin ? "default" : "outline"}>
                              {user.is_admin ? "Yes" : "No"}
                            </Badge>
                          </TableCell>
                          <TableCell>
                            <Button
                              size="sm"
                              onClick={(e) => {
                                e.stopPropagation();
                                handleAddUserToTeam(user.id, e);
                              }}
                              disabled={addUserToTeamMutation.isPending}
                            >
                              {addUserToTeamMutation.isPending ? (
                                <Loader2 className="h-4 w-4 animate-spin" />
                              ) : (
                                "Add"
                              )}
                            </Button>
                          </TableCell>
                        </TableRow>
                      ))}
                    </TableBody>
                  </Table>
                </div>
              </div>
            )}

            {searchQuery && !isSearching && searchResults.length === 0 && (
              <div className="text-center py-4">
                <p className="text-muted-foreground">No users found matching your search.</p>
              </div>
            )}

            <DialogFooter>
              <Button
                type="button"
                variant="outline"
                onClick={() => {
                  setIsAddingUserToTeam(false);
                  setSearchQuery('');
                  setSearchResults([]);
                }}
              >
                Close
              </Button>
            </DialogFooter>
          </DialogContent>
        </Dialog>

        {/* Dialog for creating a new user in a team */}
        <Dialog open={isCreatingUserInTeam} onOpenChange={setIsCreatingUserInTeam}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New User</DialogTitle>
              <DialogDescription>
                Create a new user and add them to this team.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateUserInTeam}>
              <div className="grid gap-4 py-4">
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
                {!isPasswordless && (
                  <div className="space-y-2">
                    <label className="text-sm font-medium">Password</label>
                    <Input
                      type="password"
                      value={newUserPassword}
                      onChange={(e) => setNewUserPassword(e.target.value)}
                      placeholder="••••••••"
                      required
                    />
                  </div>
                )}
                <div className="space-y-2">
                  <label className="text-sm font-medium">Role</label>
                  <Select
                    value={newUserRole}
                    onValueChange={setNewUserRole}
                  >
                    <SelectTrigger className="col-span-3">
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
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsCreatingUserInTeam(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createUserMutation.isPending}
                >
                  {createUserMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Create User
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {/* Dialog for subscribing a team to a product */}
        <Dialog open={isSubscribingToProduct} onOpenChange={setIsSubscribingToProduct}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Subscribe Team to Product</DialogTitle>
              <DialogDescription>
                Select a product to subscribe this team to.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateTeamSubscription}>
              <div className="grid gap-4 py-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Product</label>
                  <Select
                    value={selectedProductId}
                    onValueChange={setSelectedProductId}
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select a product" />
                    </SelectTrigger>
                    <SelectContent>
                      {isLoadingAllProducts ? (
                        <SelectItem value="" disabled>
                          Loading products...
                        </SelectItem>
                      ) : allProducts.length > 0 ? (
                        allProducts.map((product) => (
                          <SelectItem key={product.id} value={product.id}>
                            {product.name} ({product.id})
                          </SelectItem>
                        ))
                      ) : (
                        <SelectItem value="" disabled>
                          No products available
                        </SelectItem>
                      )}
                    </SelectContent>
                  </Select>
                </div>
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => {
                    setIsSubscribingToProduct(false);
                    setSelectedProductId('');
                  }}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createTeamSubscriptionMutation.isPending || !selectedProductId}
                >
                  {createTeamSubscriptionMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Subscribe
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {/* Dialog for merging teams */}
        <Dialog open={isMergingTeams} onOpenChange={setIsMergingTeams}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Merge Teams</DialogTitle>
              <DialogDescription>
                Merge another team into the target team. Users and keys from the source team will be moved to the target team, and the source team will be deleted. This action cannot be undone.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleMergeTeams} className="space-y-4">
              <div className="space-y-2">
                <label className="text-sm font-medium">Target Team</label>
                <Select
                  value={selectedTeamId}
                  onValueChange={setSelectedTeamId}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select target team" />
                  </SelectTrigger>
                  <SelectContent>
                    {teams.map((team) => (
                      <SelectItem key={team.id} value={team.name}>
                        {team.name} ({team.id})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <Alert>
                <AlertDescription>
                  <strong>Warning:</strong> This operation will permanently delete the source team after merging its users and keys into the target team. Make sure you have selected the correct teams.
                </AlertDescription>
              </Alert>
              <div className="space-y-2">
                <label className="text-sm font-medium">Source Team</label>
                <Select
                  value={selectedSourceTeamId}
                  onValueChange={setSelectedSourceTeamId}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select a team to merge from" />
                  </SelectTrigger>
                  <SelectContent>
                    {teams.filter(team => team.name !== selectedTeamId).map((team) => (
                      <SelectItem key={team.id} value={team.name}>
                        {team.name} ({team.id})
                      </SelectItem>
                    ))}
                  </SelectContent>
                </Select>
              </div>
              <div className="space-y-2">
                <label className="text-sm font-medium">Conflict Resolution</label>
                <Select
                  value={conflictResolutionStrategy}
                  onValueChange={(value: 'delete' | 'rename' | 'cancel') => setConflictResolutionStrategy(value)}
                >
                  <SelectTrigger>
                    <SelectValue placeholder="Select a strategy" />
                  </SelectTrigger>
                  <SelectContent>
                    <SelectItem value="delete">Delete conflicting keys from source team</SelectItem>
                    <SelectItem value="rename">Rename conflicting keys from source team</SelectItem>
                    <SelectItem value="cancel">Cancel merge if conflicts exist</SelectItem>
                  </SelectContent>
                </Select>
              </div>
              {conflictResolutionStrategy === 'rename' && (
                <div className="space-y-2">
                  <label className="text-sm font-medium">Rename Suffix</label>
                  <Input
                    value={renameSuffix}
                    onChange={(e) => setRenameSuffix(e.target.value)}
                    placeholder="e.g., _merged"
                  />
                </div>
              )}
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsMergingTeams(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={mergeTeamsMutation.isPending || !selectedTeamId || !selectedSourceTeamId}
                >
                  {mergeTeamsMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Merge Teams
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>

        {/* Dialog for adding a new team */}
        <Dialog open={isAddingTeam} onOpenChange={setIsAddingTeam}>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Add New Team</DialogTitle>
              <DialogDescription>
                Create a new team by filling out the information below.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateTeam}>
              <div className="grid gap-4 py-4">
                <div className="grid grid-cols-4 items-center gap-4">
                  <label htmlFor="name" className="text-right">
                    Name
                  </label>
                  <Input
                    id="name"
                    value={newTeamName}
                    onChange={(e) => setNewTeamName(e.target.value)}
                    className="col-span-3"
                    required
                  />
                </div>
                <div className="grid grid-cols-4 items-center gap-4">
                  <label htmlFor="admin_email" className="text-right">
                    Admin Email
                  </label>
                  <Input
                    id="admin_email"
                    type="email"
                    value={newTeamAdminEmail}
                    onChange={(e) => setNewTeamAdminEmail(e.target.value)}
                    className="col-span-3"
                    required
                  />
                </div>
              </div>
              <DialogFooter>
                <Button
                  type="button"
                  variant="outline"
                  onClick={() => setIsAddingTeam(false)}
                >
                  Cancel
                </Button>
                <Button
                  type="submit"
                  disabled={createTeamMutation.isPending}
                >
                  {createTeamMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Create Team
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>
    </div>
  );
}