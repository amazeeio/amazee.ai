'use client';

import { useState } from 'react';
import { useMutation, useQueryClient, useQuery } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import { get, post } from '@/utils/api';
import { PrivateAIKeysTable } from '@/components/private-ai-keys-table';
import { CreateAIKeyDialog } from '@/components/create-ai-key-dialog';
import { useAuth } from '@/hooks/use-auth';
import { usePrivateAIKeysData } from '@/hooks/use-private-ai-keys-data';



interface PrivateAIKey {
  database_name: string;
  name: string;
  database_host: string;
  database_username: string;
  database_password: string;
  litellm_token: string;
  litellm_api_url: string;
  region: string;
  id: number;
  owner_id: number;
  team_id?: number;
  created_at: string;
}

export default function DashboardPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const { user } = useAuth();
  const [isCreateDialogOpen, setIsCreateDialogOpen] = useState(false);

  // Fetch private AI keys using React Query
  const { data: privateAIKeys = [] } = useQuery<PrivateAIKey[]>({
    queryKey: ['private-ai-keys', user?.id],
    queryFn: async () => {
      if (!user) return [];
      const response = await get(`/private-ai-keys?owner_id=${user.id}`);
      const data = await response.json();
      return data;
    },
    enabled: !!user,
    staleTime: 0, // Always consider data stale to ensure immediate refetch
    refetchOnWindowFocus: false, // Prevent unnecessary refetches
  });

  // Use shared hook for data fetching (only for team details and regions)
  const { teamDetails, teamMembers, regions } = usePrivateAIKeysData(privateAIKeys, new Set());

  // Update budget period mutation
  const updateBudgetMutation = useMutation({
    mutationFn: async ({ keyId, budgetDuration }: { keyId: number; budgetDuration: string }) => {
      const response = await post(`/private-ai-keys/${keyId}/budget-period`, { budget_duration: budgetDuration });
      return response.json();
    },
    onSuccess: (_, { keyId }) => {
      // Invalidate the specific key's spend query to refresh the data
      queryClient.invalidateQueries({ queryKey: ['private-ai-key-spend', keyId] });
      toast({
        title: 'Success',
        description: 'Budget period updated successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to update budget period',
        variant: 'destructive',
      });
    },
  });

  // Delete key mutation
  const deleteKeyMutation = useMutation({
    mutationFn: async (keyId: number) => {
      const response = await post(`/private-ai-keys/${keyId}/delete`, {});
      return response.json();
    },
    onSuccess: () => {
      // Invalidate and refetch the private AI keys query
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      queryClient.refetchQueries({ queryKey: ['private-ai-keys'], exact: true });
      toast({
        title: 'Success',
        description: 'Key deleted successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to delete key',
        variant: 'destructive',
      });
    },
  });

  // Create key mutation
  const createKeyMutation = useMutation({
    mutationFn: async ({ region_id, name, key_type, owner_id, team_id }: {
      region_id: number,
      name: string,
      key_type: 'full' | 'llm' | 'vector',
      owner_id?: number,
      team_id?: number
    }) => {
      const endpoint = key_type === 'full' ? '/private-ai-keys' :
                      key_type === 'llm' ? '/private-ai-keys/token' :
                      '/private-ai-keys/vector-db';
      const payload: { region_id: number; name: string; owner_id?: number; team_id?: number } = { region_id, name };
      if (owner_id) payload.owner_id = owner_id;
      if (team_id) payload.team_id = team_id;
      const response = await post(endpoint, payload);
      const data = await response.json();
      return data;
    },
    onSuccess: () => {
      // Invalidate and refetch the private AI keys query
      queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      queryClient.refetchQueries({ queryKey: ['private-ai-keys'], exact: true });
      setIsCreateDialogOpen(false);
      toast({
        title: 'Success',
        description: 'Private AI key created successfully',
      });
    },
    onError: () => {
      toast({
        title: 'Error',
        description: 'Failed to create key',
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
    createKeyMutation.mutate({
      region_id: data.region_id,
      name: data.name,
      key_type: data.key_type,
      owner_id: data.owner_id,
      team_id: data.team_id
    });
  };

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">My Private AI Keys</h1>
        {user?.role !== 'read_only' && user && (
          <CreateAIKeyDialog
            open={isCreateDialogOpen}
            onOpenChange={setIsCreateDialogOpen}
            onSubmit={handleCreateKey}
            isLoading={createKeyMutation.isPending}
            regions={regions}
            showUserAssignment={true}
            currentUser={user}
            triggerText="Create Private AI Key"
            title="Create Private AI Key"
            description="Select a region and provide a name for your new private AI key."
          />
        )}
      </div>

      <PrivateAIKeysTable
        keys={privateAIKeys}
        onDelete={(keyId) => deleteKeyMutation.mutate(keyId)}
        isLoading={createKeyMutation.isPending}
        showOwner={true}
        allowModification={false}
        onUpdateBudget={(keyId, budgetDuration) => updateBudgetMutation.mutate({ keyId, budgetDuration })}
        isDeleting={deleteKeyMutation.isPending}
        isUpdatingBudget={updateBudgetMutation.isPending}
        teamDetails={teamDetails}
        teamMembers={teamMembers}
      />

      {privateAIKeys.length === 0 && (
        <Card>
          <CardContent className="p-6">
            <p className="text-center text-muted-foreground">
              You don&apos;t have any private AI keys yet. Create your first key to get started.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}