'use client';

import { useState, useCallback } from 'react';
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
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [isAddingKey, setIsAddingKey] = useState(false);

  // Fetch team members
  const { data: teamMembersFull = [] } = useQuery<TeamUser[]>({
    queryKey: ['team-members'],
    queryFn: async () => {
      const response = await get('teams/members', { credentials: 'include' });
      return response.json();
    },
  });

  // Fetch private AI keys
  const { data: keys = [], isLoading: isLoadingKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['private-ai-keys'],
    queryFn: async () => {
      const response = await get('private-ai-keys', { credentials: 'include' });
      return response.json();
    },
  });

  // Use shared hook for data fetching (only for team details and regions)
  const { teamDetails, teamMembers, regions } = usePrivateAIKeysData(keys, new Set());

  // Create key mutation
  const createKeyMutation = useMutation({
    mutationFn: async (data: {
      name: string
      region_id: number
      key_type: 'full' | 'llm' | 'vector'
      owner_id?: number
      team_id?: number
    }) => {
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
        description: 'AI key created successfully',
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

  // Delete key mutation
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
      // Invalidate the specific key's spend query to refresh the data
      queryClient.invalidateQueries({ queryKey: ['private-ai-key-spend', variables.keyId] });
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