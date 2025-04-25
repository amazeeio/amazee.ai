'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
  DialogTrigger,
} from '@/components/ui/dialog';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Plus } from 'lucide-react';
import { get, post, del, put } from '@/utils/api';
import { useAuth } from '@/hooks/use-auth';
import { PrivateAIKeysTable } from '@/components/private-ai-keys-table';
import { PrivateAIKey } from '@/types/private-ai-key';

interface SpendInfo {
  spend: number;
  expires: string;
  created_at: string;
  updated_at: string;
  max_budget: number | null;
  budget_duration: string | null;
  budget_reset_at: string | null;
}

interface Region {
  id: number;
  name: string;
  is_active: boolean;
}

interface TeamUser {
  id: number;
  email: string;
  is_active: boolean;
  role: string;
  team_id: number | null;
  created_at: string;
}

export default function TeamAIKeysPage() {
  const { toast } = useToast();
  const { user } = useAuth();
  const [isAddingKey, setIsAddingKey] = useState(false);
  const [newKeyName, setNewKeyName] = useState('');
  const [selectedRegion, setSelectedRegion] = useState<string>('');
  const [selectedUserId, setSelectedUserId] = useState<string>('team');
  const [loadedSpendKeys, setLoadedSpendKeys] = useState<Set<number>>(new Set());
  const [openBudgetDialog, setOpenBudgetDialog] = useState<number | null>(null);

  const queryClient = useQueryClient();

  const { data: keys = [], isLoading: isLoadingKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['private-ai-keys', user?.team_id],
    queryFn: async () => {
      const response = await get(`private-ai-keys?team_id=${user?.team_id}`, { credentials: 'include' });
      const data = await response.json();
      return data;
    },
    enabled: !!user?.team_id,
  });

  // Get unique team IDs from the keys
  const teamIds = Array.from(new Set(keys.filter(key => key.team_id).map(key => key.team_id)));

  // Fetch team details for each team ID
  const { data: teamDetails = {} } = useQuery({
    queryKey: ['team-details', teamIds],
    queryFn: async () => {
      const teamPromises = teamIds.map(async (teamId) => {
        const response = await get(`teams/${teamId}`, { credentials: 'include' });
        const data = await response.json();
        return [teamId, data];
      });
      const teamResults = await Promise.all(teamPromises);
      return Object.fromEntries(teamResults);
    },
    enabled: teamIds.length > 0,
  });

  const { data: teams = [] } = useQuery({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('teams', { credentials: 'include' });
      const data = await response.json();
      console.log('Teams list:', data);
      return data;
    },
  });

  const { data: regions = [] } = useQuery<Region[]>({
    queryKey: ['regions'],
    queryFn: async () => {
      const response = await get('regions', { credentials: 'include' });
      return response.json();
    },
  });

  const { data: teamMembers = [] } = useQuery<TeamUser[]>({
    queryKey: ['team-users'],
    queryFn: async () => {
      const response = await get('users', { credentials: 'include' });
      const allUsers = await response.json();
      return allUsers;
    },
  });

  // Query to get all users for displaying emails
  const { data: usersMap = {} } = useQuery<Record<number, { id: number; email: string }>>({
    queryKey: ['users-map'],
    queryFn: async () => {
      const response = await get('users', { credentials: 'include' });
      const users = await response.json();
      return users.reduce((acc: Record<number, { id: number; email: string }>, user: TeamUser) => ({
        ...acc,
        [user.id]: { id: user.id, email: user.email }
      }), {});
    },
  });

  // Query to get spend information for each key
  const { data: spendMap = {} } = useQuery<Record<number, SpendInfo>>({
    queryKey: ['private-ai-keys-spend', Array.from(loadedSpendKeys)],
    queryFn: async () => {
      const spendPromises = Array.from(loadedSpendKeys).map(async (keyId) => {
        const response = await get(`private-ai-keys/${keyId}/spend`, { credentials: 'include' });
        return [keyId, await response.json()] as [number, SpendInfo];
      });
      const spendResults = await Promise.all(spendPromises);
      return Object.fromEntries(spendResults);
    },
    enabled: loadedSpendKeys.size > 0,
  });

  const createKeyMutation = useMutation({
    mutationFn: async (data: { name: string; region_id: number; owner_id?: number; team_id?: number }) => {
      const response = await post('private-ai-keys', data, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      setIsAddingKey(false);
      setNewKeyName('');
      setSelectedRegion('');
      setSelectedUserId('team');
      toast({
        title: 'Success',
        description: 'AI key added successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to add AI key',
        variant: 'destructive',
      });
    },
  });

  const deleteKeyMutation = useMutation({
    mutationFn: async (keyId: number) => {
      const response = await del(`private-ai-keys/${keyId}`, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      toast({
        title: 'Success',
        description: 'AI key deleted successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete AI key',
        variant: 'destructive',
      });
    },
  });

  // Add mutation for updating budget period
  const updateBudgetPeriodMutation = useMutation({
    mutationFn: async ({ keyId, budgetDuration }: { keyId: number; budgetDuration: string }) => {
      const response = await put(`private-ai-keys/${keyId}/budget-period`, {
        budget_duration: budgetDuration
      }, { credentials: 'include' });
      return response.json();
    },
    onSuccess: (data, variables) => {
      // Update the spend information for this specific key
      queryClient.setQueryData(['private-ai-keys-spend', Array.from(loadedSpendKeys)], (oldData: Record<number, SpendInfo> = {}) => ({
        ...oldData,
        [variables.keyId]: data
      }));
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

  const handleCreateKey = (e: React.FormEvent) => {
    e.preventDefault();
    const region = regions.find(r => r.name === selectedRegion);
    if (!region || !user?.team_id) return;

    const data: { name: string; region_id: number; owner_id?: number; team_id?: number } = {
      name: newKeyName,
      region_id: region.id,
    };

    if (selectedUserId === 'team') {
      data.team_id = user.team_id;
    } else {
      data.owner_id = parseInt(selectedUserId);
    }

    createKeyMutation.mutate(data);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Team AI Keys</h1>
        <Dialog open={isAddingKey} onOpenChange={setIsAddingKey}>
          <DialogTrigger asChild>
            <Button>
              <Plus className="mr-2 h-4 w-4" />
              Add Key
            </Button>
          </DialogTrigger>
          <DialogContent>
            <DialogHeader>
              <DialogTitle>Create New AI Key</DialogTitle>
              <DialogDescription>
                Create a new AI key for your team. This will generate new database credentials.
              </DialogDescription>
            </DialogHeader>
            <form onSubmit={handleCreateKey}>
              <div className="grid gap-4 py-4">
                <div className="grid gap-2">
                  <label htmlFor="name">Name</label>
                  <Input
                    id="name"
                    value={newKeyName}
                    onChange={(e) => setNewKeyName(e.target.value)}
                    required
                  />
                </div>
                <div className="grid gap-2">
                  <label htmlFor="region">Region</label>
                  <Select value={selectedRegion} onValueChange={setSelectedRegion}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select a region" />
                    </SelectTrigger>
                    <SelectContent>
                      {regions
                        .filter(region => region.is_active)
                        .map(region => (
                          <SelectItem key={region.id} value={region.name}>
                            {region.name}
                          </SelectItem>
                        ))}
                    </SelectContent>
                  </Select>
                </div>
                <div className="grid gap-2">
                  <label htmlFor="user">Assign to <span className="text-red-500">*</span></label>
                  <Select
                    value={selectedUserId}
                    onValueChange={setSelectedUserId}
                    required
                  >
                    <SelectTrigger>
                      <SelectValue placeholder="Select who will own this key" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="team">Team (Shared)</SelectItem>
                      {teamMembers.map(member => (
                        <SelectItem key={member.id} value={member.id.toString()}>
                          {member.email}
                        </SelectItem>
                      ))}
                    </SelectContent>
                  </Select>
                  <p className="text-sm text-muted-foreground">
                    Select "Team (Shared)" to create a key accessible to all team members
                  </p>
                </div>
              </div>
              <DialogFooter>
                <Button type="submit" disabled={createKeyMutation.isPending || !selectedRegion || !selectedUserId}>
                  {createKeyMutation.isPending && (
                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                  )}
                  Create Key
                </Button>
              </DialogFooter>
            </form>
          </DialogContent>
        </Dialog>
      </div>

      <PrivateAIKeysTable
        keys={keys}
        onDelete={deleteKeyMutation.mutate}
        isLoading={isLoadingKeys}
        isDeleting={deleteKeyMutation.isPending}
        showSpend={true}
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