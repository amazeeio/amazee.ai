'use client';

import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient, useQueries } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Search } from 'lucide-react';
import { get, del, put } from '@/utils/api';
import { useDebounce } from '@/hooks/use-debounce';
import {
  Command,
  CommandEmpty,
  CommandGroup,
  CommandInput,
  CommandItem,
  CommandList,
} from '@/components/ui/command';
import {
  Popover,
  PopoverContent,
  PopoverTrigger,
} from '@/components/ui/popover';
import { PrivateAIKeysTable } from '@/components/private-ai-keys-table';
import { PrivateAIKey } from '@/types/private-ai-key';

interface User {
  id: number;
  email: string;
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

export default function PrivateAIKeysPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [searchTerm, setSearchTerm] = useState('');
  const [isUserSearchOpen, setIsUserSearchOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [loadedSpendKeys, setLoadedSpendKeys] = useState<Set<number>>(new Set());
  const debouncedSearchTerm = useDebounce(searchTerm, 300);

  // Queries
  const { data: privateAIKeys = [], isLoading: isLoadingPrivateAIKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['private-ai-keys', selectedUser?.id],
    queryFn: async () => {
      const url = selectedUser?.id
        ? `/private-ai-keys?owner_id=${selectedUser.id}`
        : '/private-ai-keys';
      const response = await get(url);
      const data = await response.json();
      return data;
    },
  });

  // Get unique team IDs from the keys
  const teamIds = Array.from(new Set(privateAIKeys.filter(key => key.team_id).map(key => key.team_id)));

  // Fetch team details for each team ID
  const { data: teamDetails = {} } = useQuery({
    queryKey: ['team-details', teamIds],
    queryFn: async () => {
      const teamPromises = teamIds.map(async (teamId) => {
        const response = await get(`teams/${teamId}`);
        const data = await response.json();
        return [teamId, data];
      });
      const teamResults = await Promise.all(teamPromises);
      return Object.fromEntries(teamResults);
    },
    enabled: teamIds.length > 0,
  });

  // Query to get all users for displaying emails
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

  const { data: users = [], isLoading: isLoadingUsers, isFetching: isFetchingUsers } = useQuery<User[], Error, User[]>({
    queryKey: ['users', debouncedSearchTerm],
    queryFn: async () => {
      if (!debouncedSearchTerm) return [];
      await new Promise(resolve => setTimeout(resolve, 100)); // Small delay to ensure loading state shows
      const response = await get(`/users/search?email=${encodeURIComponent(debouncedSearchTerm)}`);
      const data = await response.json();
      return data;
    },
    enabled: isUserSearchOpen && !!debouncedSearchTerm,
    gcTime: 60000,
    staleTime: 30000,
    refetchOnMount: false,
    refetchOnWindowFocus: false,
  });

  // Show loading state immediately when search term changes
  const isSearching = searchTerm.length > 0 && (
    isLoadingUsers ||
    isFetchingUsers ||
    debouncedSearchTerm !== searchTerm
  );

  const handleSearchChange = (value: string) => {
    setSearchTerm(value);
    // Prefetch the query if we have a value
    if (value) {
      queryClient.prefetchQuery({
        queryKey: ['users', value],
        queryFn: async () => {
          const response = await get(`/users/search?email=${encodeURIComponent(value)}`);
          const data = await response.json();
          return data;
        },
      });
    }
  };

  // Query to get spend information for each key
  const spendQueries = useQueries({
    queries: privateAIKeys
      .filter(key => loadedSpendKeys.has(key.id))
      .map((key) => ({
        queryKey: ['private-ai-key-spend', key.id],
        queryFn: async () => {
          const response = await get(`/private-ai-keys/${key.id}/spend`);
          const data = await response.json();
          return data as SpendInfo;
        },
        refetchInterval: 60000, // Refetch every minute
      })),
  });

  // Create a map of spend information
  const spendMap = useMemo(() => {
    return spendQueries.reduce((acc, query, index) => {
      if (query.data) {
        acc[privateAIKeys.filter(key => loadedSpendKeys.has(key.id))[index].id] = query.data;
      }
      return acc;
    }, {} as Record<number, SpendInfo>);
  }, [spendQueries, privateAIKeys, loadedSpendKeys]);

  // Mutations
  const deletePrivateAIKeyMutation = useMutation({
    mutationFn: async (keyId: number) => {
      await del(`/private-ai-keys/${keyId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      toast({
        title: 'Success',
        description: 'Private AI key deleted successfully',
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

  // Add mutation for updating budget period
  const updateBudgetPeriodMutation = useMutation({
    mutationFn: async ({ keyId, budgetDuration }: { keyId: number; budgetDuration: string }) => {
      const response = await put(`/private-ai-keys/${keyId}/budget-period`, {
        budget_duration: budgetDuration
      });
      return response.json();
    },
    onSuccess: (data, variables) => {
      // Update the spend information for this specific key
      queryClient.setQueryData(['private-ai-key-spend', variables.keyId], data);
      toast({
        title: 'Success',
        description: 'Budget period updated successfully',
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

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Private AI Keys</h1>
      </div>

      <div className="flex items-center gap-2">
        <Popover open={isUserSearchOpen} onOpenChange={setIsUserSearchOpen}>
          <PopoverTrigger asChild>
            <Button variant="outline" className="w-[250px] justify-between">
              {selectedUser ? selectedUser.email : 'Filter by owner...'}
              <Search className="ml-2 h-4 w-4 shrink-0 opacity-50" />
            </Button>
          </PopoverTrigger>
          <PopoverContent className="w-[250px] p-0">
            <Command>
              <CommandInput
                placeholder="Search users..."
                value={searchTerm}
                onValueChange={handleSearchChange}
              />
              <CommandList>
                {!searchTerm ? (
                  <div className="py-6 text-center text-sm text-muted-foreground">
                    Start typing to search users...
                  </div>
                ) : isSearching ? (
                  <div className="py-6 text-center text-sm">
                    <Loader2 className="mx-auto h-4 w-4 animate-spin" />
                    <p className="mt-2">Searching users...</p>
                  </div>
                ) : users.length === 0 ? (
                  <CommandEmpty>No users found.</CommandEmpty>
                ) : (
                  <CommandGroup>
                    {users.map((user) => (
                      <CommandItem
                        key={user.id}
                        onSelect={() => {
                          setSelectedUser(user);
                          setIsUserSearchOpen(false);
                          setSearchTerm('');
                        }}
                      >
                        {user.email}
                      </CommandItem>
                    ))}
                  </CommandGroup>
                )}
              </CommandList>
            </Command>
          </PopoverContent>
        </Popover>
        {selectedUser && (
          <Button
            variant="ghost"
            onClick={() => {
              setSelectedUser(null);
              setSearchTerm('');
            }}
          >
            Clear filter
          </Button>
        )}
      </div>

      <PrivateAIKeysTable
        keys={privateAIKeys}
        onDelete={deletePrivateAIKeyMutation.mutate}
        isLoading={isLoadingPrivateAIKeys}
        isDeleting={deletePrivateAIKeyMutation.isPending}
        allowModification={true}
        showOwner={true}
        spendMap={spendMap}
        onLoadSpend={(keyId) => setLoadedSpendKeys(prev => new Set([...prev, keyId]))}
        onUpdateBudget={(keyId, budgetDuration) => {
          updateBudgetPeriodMutation.mutate({ keyId, budgetDuration });
        }}
        isUpdatingBudget={updateBudgetPeriodMutation.isPending}
        teamDetails={teamDetails}
        teamMembers={Object.values(usersMap)}
      />
    </div>
  );
}