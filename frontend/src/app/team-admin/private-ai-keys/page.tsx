'use client';

import { useState } from 'react';
import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { useToast } from '@/hooks/use-toast';
import { get, post, del, put } from '@/utils/api';
import { useAuth } from '@/hooks/use-auth';
import { PrivateAIKeysTable } from '@/components/private-ai-keys-table';
import { CreateAIKeyDialog } from '@/components/create-ai-key-dialog';
import { PrivateAIKey } from '@/types/private-ai-key';
import { usePrivateAIKeysData } from '@/hooks/use-private-ai-keys-data';

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
  const [loadedSpendKeys, setLoadedSpendKeys] = useState<Set<number>>(new Set());

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

  // Use shared hook for data fetching
  const { teamDetails, teamMembers, spendMap, regions } = usePrivateAIKeysData(keys, loadedSpendKeys);

  const { data: teamMembersFull = [] } = useQuery<TeamUser[]>({
    queryKey: ['team-users'],
    queryFn: async () => {
      const response = await get('users', { credentials: 'include' });
      const allUsers = await response.json();
      return allUsers;
    },
  });

  const createKeyMutation = useMutation({
    mutationFn: async (data: { name: string; region_id: number; owner_id?: number; team_id?: number; key_type: 'full' | 'llm' | 'vector' }) => {
      const endpoint = data.key_type === 'full' ? 'private-ai-keys' :
                      data.key_type === 'llm' ? 'private-ai-keys/token' :
                      'private-ai-keys/vector-db';
      const response = await post(endpoint, data, { credentials: 'include' });
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      queryClient.refetchQueries({ queryKey: ['private-ai-keys'], exact: true });
      setIsAddingKey(false);
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
      queryClient.refetchQueries({ queryKey: ['private-ai-keys'], exact: true });
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
    // Add team_id for team context
    if (user?.team_id) {
      data.team_id = user.team_id;
    }
    createKeyMutation.mutate(data);
  };

  return (
    <div className="space-y-4">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Team AI Keys</h1>
        <CreateAIKeyDialog
          open={isAddingKey}
          onOpenChange={setIsAddingKey}
          onSubmit={handleCreateKey}
          isLoading={createKeyMutation.isPending}
          regions={regions}
          teamMembers={teamMembersFull}
          showUserAssignment={true}
          currentUser={user || undefined}
          triggerText="Add Key"
          title="Create New AI Key"
          description="Create a new AI key for your team. This will generate new database credentials."
        />
      </div>

      <PrivateAIKeysTable
        keys={keys}
        onDelete={deleteKeyMutation.mutate}
        isLoading={isLoadingKeys}
        isDeleting={deleteKeyMutation.isPending}
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