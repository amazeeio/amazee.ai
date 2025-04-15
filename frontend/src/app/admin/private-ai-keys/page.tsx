'use client';

import { useState, useMemo } from 'react';
import { useQuery, useMutation, useQueryClient, useQueries } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
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
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Eye, EyeOff, Search, Pencil } from 'lucide-react';
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

interface User {
  id: number;
  email: string;
}

interface PrivateAIKey {
  database_name: string;
  name: string;  // User-friendly display name
  database_host: string;
  database_username: string;
  database_password: string;
  litellm_token: string;
  litellm_api_url: string;
  owner_id: number;
  region: string;
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

function formatTimeUntil(date: string): string {
  const now = new Date();
  const resetDate = new Date(date);
  const diffMs = resetDate.getTime() - now.getTime();
  const diffHours = Math.floor(diffMs / (1000 * 60 * 60));
  const diffMinutes = Math.floor((diffMs % (1000 * 60 * 60)) / (1000 * 60));

  if (diffHours > 0) {
    return `in ${diffHours}h ${diffMinutes}m`;
  }
  return `in ${diffMinutes}m`;
}

export default function PrivateAIKeysPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [visibleCredentials, setVisibleCredentials] = useState<Set<string>>(new Set());
  const [searchTerm, setSearchTerm] = useState('');
  const [isUserSearchOpen, setIsUserSearchOpen] = useState(false);
  const [selectedUser, setSelectedUser] = useState<User | null>(null);
  const [openBudgetDialog, setOpenBudgetDialog] = useState<string | null>(null);
  const [loadedSpendKeys, setLoadedSpendKeys] = useState<Set<string>>(new Set());
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
      .filter(key => loadedSpendKeys.has(key.database_name))
      .map((key) => ({
        queryKey: ['private-ai-key-spend', key.database_name],
        queryFn: async () => {
          const response = await get(`/private-ai-keys/${key.database_name}/spend`);
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
        acc[privateAIKeys.filter(key => loadedSpendKeys.has(key.database_name))[index].database_name] = query.data;
      }
      return acc;
    }, {} as Record<string, SpendInfo>);
  }, [spendQueries, privateAIKeys, loadedSpendKeys]);

  // Mutations
  const deletePrivateAIKeyMutation = useMutation({
    mutationFn: async (keyName: string) => {
      await del(`/private-ai-keys/${keyName}`);
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
    mutationFn: async ({ keyName, budgetDuration }: { keyName: string; budgetDuration: string }) => {
      const response = await put(`/private-ai-keys/${keyName}/budget-period`, {
        budget_duration: budgetDuration
      });
      return response.json();
    },
    onSuccess: (data, variables) => {
      // Update the spend information for this specific key
      queryClient.setQueryData(['private-ai-key-spend', variables.keyName], data);
      setOpenBudgetDialog(null); // Close the dialog
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

  if (isLoadingPrivateAIKeys) {
    return (
      <div className="flex items-center justify-center min-h-[400px]">
        <Loader2 className="h-8 w-8 animate-spin" />
      </div>
    );
  }

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

      <div className="rounded-md border">
        <Table>
          <TableHeader>
            <TableRow>
              <TableHead>Name</TableHead>
              <TableHead>Credentials</TableHead>
              <TableHead>Region</TableHead>
              <TableHead>Owner</TableHead>
              <TableHead>Spend</TableHead>
              <TableHead>Actions</TableHead>
            </TableRow>
          </TableHeader>
          <TableBody>
            {privateAIKeys.map((key) => (
              <TableRow key={key.database_name}>
                <TableCell>{key.name || key.database_name}</TableCell>
                <TableCell>
                  <div className="space-y-2">
                    <div className="flex items-center gap-2">
                      <span>Database: {key.database_name}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span>Host: {key.database_host}</span>
                    </div>
                    <div className="flex items-center gap-2">
                      <span>Username: {key.database_username}</span>
                    </div>
                    {key.database_password && (
                      <div className="flex items-center gap-2">
                        <span>Password:</span>
                        {visibleCredentials.has(`${key.database_name}-password`) ? (
                          <div className="flex items-center gap-2">
                            <code className="px-2 py-1 bg-muted rounded text-sm font-mono">
                              {key.database_password}
                            </code>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => {
                                setVisibleCredentials(prev => {
                                  const next = new Set(prev);
                                  next.delete(`${key.database_name}-password`);
                                  return next;
                                });
                              }}
                            >
                              <EyeOff className="h-4 w-4" />
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              setVisibleCredentials(prev => {
                                const next = new Set(prev);
                                next.add(`${key.database_name}-password`);
                                return next;
                              });
                            }}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    )}
                    {key.litellm_token && (
                      <div className="flex items-center gap-2">
                        <span>LLM Key:</span>
                        {visibleCredentials.has(`${key.database_name}-token`) ? (
                          <div className="flex items-center gap-2">
                            <code className="px-2 py-1 bg-muted rounded text-sm font-mono">
                              {key.litellm_token}
                            </code>
                            <Button
                              variant="ghost"
                              size="icon"
                              onClick={() => {
                                setVisibleCredentials(prev => {
                                  const next = new Set(prev);
                                  next.delete(`${key.database_name}-token`);
                                  return next;
                                });
                              }}
                            >
                              <EyeOff className="h-4 w-4" />
                            </Button>
                          </div>
                        ) : (
                          <Button
                            variant="ghost"
                            size="icon"
                            onClick={() => {
                              setVisibleCredentials(prev => {
                                const next = new Set(prev);
                                next.add(`${key.database_name}-token`);
                                return next;
                              });
                            }}
                          >
                            <Eye className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                </TableCell>
                <TableCell>
                  <span className="inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium bg-blue-100 text-blue-800">
                    {key.region}
                  </span>
                </TableCell>
                <TableCell>
                  <div className="flex flex-col gap-1">
                    <span className="text-sm">{usersMap[key.owner_id]?.email || 'Unknown'}</span>
                    <span className="text-xs text-muted-foreground">ID: {key.owner_id}</span>
                  </div>
                </TableCell>
                <TableCell>
                  {loadedSpendKeys.has(key.database_name) ? (
                    spendMap[key.database_name] ? (
                      <div className="flex flex-col gap-1">
                        <div className="flex items-center gap-2">
                          <span className="text-sm font-medium">
                            ${spendMap[key.database_name].spend.toFixed(2)}
                          </span>
                          <span className="text-xs text-muted-foreground">
                            {spendMap[key.database_name]?.max_budget !== null 
                              ? `/ $${spendMap[key.database_name]?.max_budget?.toFixed(2)}`
                              : '(No budget)'}
                          </span>
                        </div>
                        <span className="text-xs text-muted-foreground">
                          {spendMap[key.database_name].budget_duration || 'No budget period'}
                          {spendMap[key.database_name].budget_reset_at && ` • Resets ${formatTimeUntil(spendMap[key.database_name].budget_reset_at as string)}`}
                          <Dialog open={openBudgetDialog === key.database_name} onOpenChange={(open) => setOpenBudgetDialog(open ? key.database_name : null)}>
                            <DialogTrigger asChild>
                              <Button variant="ghost" size="icon" className="h-4 w-4 ml-1">
                                <Pencil className="h-3 w-3" />
                              </Button>
                            </DialogTrigger>
                            <DialogContent>
                              <DialogHeader>
                                <DialogTitle>Update Budget Period</DialogTitle>
                                <DialogDescription>
                                  Set the budget period for this key. Examples: &quot;30d&quot; (30 days), &quot;24h&quot; (24 hours), &quot;60m&quot; (60 minutes)
                                </DialogDescription>
                              </DialogHeader>
                              <div className="grid gap-4 py-4">
                                <div className="grid gap-2">
                                  <Label htmlFor="budget-duration">Budget Period</Label>
                                  <Input
                                    id="budget-duration"
                                    defaultValue={spendMap[key.database_name].budget_duration || ''}
                                    placeholder="e.g. 30d"
                                  />
                                </div>
                              </div>
                              <DialogFooter>
                                <Button
                                  onClick={() => {
                                    const input = document.getElementById('budget-duration') as HTMLInputElement;
                                    if (input) {
                                      updateBudgetPeriodMutation.mutate({
                                        keyName: key.database_name,
                                        budgetDuration: input.value
                                      });
                                    }
                                  }}
                                  disabled={updateBudgetPeriodMutation.isPending}
                                >
                                  {updateBudgetPeriodMutation.isPending ? (
                                    <>
                                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                      Updating...
                                    </>
                                  ) : (
                                    'Update'
                                  )}
                                </Button>
                              </DialogFooter>
                            </DialogContent>
                          </Dialog>
                        </span>
                      </div>
                    ) : (
                      <span className="text-sm text-muted-foreground">Loading...</span>
                    )
                  ) : (
                    <Button
                      variant="outline"
                      size="sm"
                      onClick={() => {
                        setLoadedSpendKeys(prev => new Set([...prev, key.database_name]));
                      }}
                    >
                      Load Spend
                    </Button>
                  )}
                </TableCell>
                <TableCell>
                  <AlertDialog>
                    <AlertDialogTrigger asChild>
                      <Button variant="destructive" size="sm">Delete</Button>
                    </AlertDialogTrigger>
                    <AlertDialogContent>
                      <AlertDialogHeader>
                        <AlertDialogTitle>Delete Private AI Key</AlertDialogTitle>
                        <AlertDialogDescription>
                          Are you sure you want to delete this private AI key? This action cannot be undone.
                        </AlertDialogDescription>
                      </AlertDialogHeader>
                      <AlertDialogFooter>
                        <AlertDialogCancel>Cancel</AlertDialogCancel>
                        <AlertDialogAction
                          onClick={() => deletePrivateAIKeyMutation.mutate(key.database_name)}
                          className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                        >
                          {deletePrivateAIKeyMutation.isPending ? (
                            <>
                              <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                              Deleting...
                            </>
                          ) : (
                            'Delete'
                          )}
                        </AlertDialogAction>
                      </AlertDialogFooter>
                    </AlertDialogContent>
                  </AlertDialog>
                </TableCell>
              </TableRow>
            ))}
          </TableBody>
        </Table>
      </div>
    </div>
  );
}