'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Search } from 'lucide-react';
import { get, del, put, post } from '@/utils/api';
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
import { CreateAIKeyDialog } from '@/components/create-ai-key-dialog';
import { PrivateAIKey } from '@/types/private-ai-key';
import { usePrivateAIKeysData } from '@/hooks/use-private-ai-keys-data';

interface User {
  id: number;
  email: string;
  is_active: boolean;
  role: string;
  team_id: number | null;
  created_at: string;
}



export default function PrivateAIKeysPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [searchTerm, setSearchTerm] = useState('');
  const [isUserSearchOpen, setIsUserSearchOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [loadedSpendKeys, setLoadedSpendKeys] = useState<Set<number>>(new Set());
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);
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
    refetchInterval: 30000, // Refetch every 30 seconds to detect new keys
    refetchIntervalInBackground: true, // Continue polling even when tab is not active
  });

  // Use shared hook for data fetching
  const { teamDetails, teamMembers, spendMap, regions } = usePrivateAIKeysData(privateAIKeys, loadedSpendKeys);

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

  // Mutations
  const createKeyMutation = useMutation({
    mutationFn: async (data: { name: string; region_id: number; owner_id?: number; team_id?: number; key_type: 'full' | 'llm' | 'vector' }) => {
      const endpoint = data.key_type === 'full' ? 'private-ai-keys' :
                      data.key_type === 'llm' ? 'private-ai-keys/token' :
                      'private-ai-keys/vector-db';
      const response = await post(endpoint, data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      queryClient.refetchQueries({ queryKey: ['private-ai-keys'], exact: true });
      setIsCreateDialogOpen(false);
      toast({
        title: 'Success',
        description: 'Private AI key created successfully',
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

  const deletePrivateAIKeyMutation = useMutation({
    mutationFn: async (keyId: number) => {
      await del(`/private-ai-keys/${keyId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      queryClient.refetchQueries({ queryKey: ['private-ai-keys'], exact: true });
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

  const handleCreateKey = (data: {
    name: string
    region_id: number
    key_type: 'full' | 'llm' | 'vector'
    owner_id?: number
    team_id?: number
  }) => {
    createKeyMutation.mutate(data);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Private AI Keys</h1>
        <CreateAIKeyDialog
          open={isCreateDialogOpen}
          onOpenChange={setIsCreateDialogOpen}
          onSubmit={handleCreateKey}
          isLoading={createKeyMutation.isPending}
          regions={regions}
          teamMembers={Object.values(usersMap)}
          showUserAssignment={true}
          currentUser={undefined}
          triggerText="Create Key"
          title="Create New Private AI Key"
          description="Create a new private AI key for any user or team."
        />
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
        teamMembers={teamMembers}
      />
    </div>
  );
}