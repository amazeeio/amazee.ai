'use client';

import { useState, useCallback, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { useToast } from '@/hooks/use-toast';
import { get, post } from '@/utils/api';
import { PrivateAIKeysTable } from '@/components/private-ai-keys-table';
import { CreateAIKeyDialog } from '@/components/create-ai-key-dialog';
import { useAuth } from '@/hooks/use-auth';
import { usePrivateAIKeysData } from '@/hooks/use-private-ai-keys-data';

interface Region {
  id: number;
  name: string;
  is_active: boolean;
}

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
  const [privateAIKeys, setPrivateAIKeys] = useState<PrivateAIKey[]>([]);
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUpdatingBudget, setIsUpdatingBudget] = useState(false);
  const [loadedSpendKeys, setLoadedSpendKeys] = useState<Set<number>>(new Set());

  // Use shared hook for data fetching
  const { teamDetails, teamMembers, spendMap, regions } = usePrivateAIKeysData(privateAIKeys, loadedSpendKeys);

  // Fetch private AI keys - only show keys owned by current user or their team
  const fetchKeys = useCallback(async () => {
    if (!user) return;

    try {
      // Filter by owner_id to only show keys owned by the current user
      const response = await get(`/private-ai-keys?owner_id=${user.id}`);
      const data = await response.json();
      setPrivateAIKeys(data);
    } catch (error) {
      console.error('Error fetching private AI keys:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch your private AI keys',
        variant: 'destructive',
      });
    }
  }, [toast, user]);

  // Load spend for a key
  const loadSpend = useCallback(async (keyId: number) => {
    setLoadedSpendKeys(prev => new Set([...prev, keyId]));
  }, []);

  // Update budget period
  const updateBudget = useCallback(async (keyId: number, budgetDuration: string) => {
    setIsUpdatingBudget(true);
    try {
      await post(`/private-ai-keys/${keyId}/budget-period`, { budget_duration: budgetDuration });
      // Refresh spend data
      setLoadedSpendKeys(prev => new Set([...prev, keyId]));
      toast({
        title: 'Success',
        description: 'Budget period updated successfully',
      });
    } catch (error) {
      console.error('Error updating budget:', error);
      toast({
        title: 'Error',
        description: 'Failed to update budget period',
        variant: 'destructive',
      });
    } finally {
      setIsUpdatingBudget(false);
    }
  }, [toast]);

  // Delete key
  const deleteKey = useCallback(async (keyId: number) => {
    setIsDeleting(true);
    try {
      await post(`/private-ai-keys/${keyId}/delete`, {});
      await fetchKeys();
      toast({
        title: 'Success',
        description: 'Key deleted successfully',
      });
    } catch (error) {
      console.error('Error deleting key:', error);
      toast({
        title: 'Error',
        description: 'Failed to delete key',
        variant: 'destructive',
      });
    } finally {
      setIsDeleting(false);
    }
  }, [toast, fetchKeys]);

  // Create key mutation
  const createKeyMutation = useMutation({
    mutationFn: async ({ region_id, name, key_type }: { region_id: number, name: string, key_type: 'full' | 'llm' | 'vector' }) => {
      const endpoint = key_type === 'full' ? '/private-ai-keys' :
                      key_type === 'llm' ? '/private-ai-keys/token' :
                      '/private-ai-keys/vector-db';
      const response = await post(endpoint, { region_id, name });
      const data = await response.json();
      return data;
    },
    onSuccess: async () => {
      await queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
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
      key_type: data.key_type
    });
  };

  useEffect(() => {
    void fetchKeys();
  }, [fetchKeys]);

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
        onDelete={deleteKey}
        isLoading={createKeyMutation.isPending}
        showOwner={true}
        allowModification={false}
        spendMap={spendMap}
        onLoadSpend={loadSpend}
        onUpdateBudget={updateBudget}
        isDeleting={isDeleting}
        isUpdatingBudget={isUpdatingBudget}
        teamDetails={teamDetails}
        teamMembers={teamMembers}
      />

      {privateAIKeys.length === 0 && (
        <Card>
          <CardContent className="p-6">
            <p className="text-center text-muted-foreground">
              You don't have any private AI keys yet. Create your first key to get started.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}