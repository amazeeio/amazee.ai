'use client';

import { useState, useCallback, useEffect } from 'react';
import { useMutation, useQueryClient } from '@tanstack/react-query';
import { Card, CardContent } from '@/components/ui/card';
import { Button } from '@/components/ui/button';
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
import { Loader2 } from 'lucide-react';
import { Input } from '@/components/ui/input';
import { get, post } from '@/utils/api';
import { PrivateAIKeysTable } from '@/components/private-ai-keys-table';
import { useAuth } from '@/hooks/use-auth';

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
  const [selectedRegion, setSelectedRegion] = useState<string>('');
  const [keyName, setKeyName] = useState<string>('');
  const [keyType, setKeyType] = useState<'full' | 'llm' | 'vector'>('full');
  const [regions, setRegions] = useState<Region[]>([]);
  const [privateAIKeys, setPrivateAIKeys] = useState<PrivateAIKey[]>([]);
  const [spendMap, setSpendMap] = useState<Record<number, {
    spend: number;
    max_budget: number | null;
    budget_duration: string | null;
    budget_reset_at: string | null;
  }>>({});
  const [isDeleting, setIsDeleting] = useState(false);
  const [isUpdatingBudget, setIsUpdatingBudget] = useState(false);

  // Fetch regions
  const fetchRegions = useCallback(async () => {
    try {
      const response = await get('regions');
      const data = await response.json();
      setRegions(data);
    } catch (error) {
      console.error('Error fetching regions:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch regions',
        variant: 'destructive',
      });
    }
  }, [toast]);

  // Fetch private AI keys
  const fetchKeys = useCallback(async () => {
    try {
      const response = await get('/private-ai-keys');
      const data = await response.json();
      setPrivateAIKeys(data);
    } catch (error) {
      console.error('Error fetching private AI keys:', error);
      toast({
        title: 'Error',
        description: 'Failed to fetch private AI keys',
        variant: 'destructive',
      });
    }
  }, [toast]);

  // Load spend for a key
  const loadSpend = useCallback(async (keyId: number) => {
    try {
      const response = await get(`/private-ai-keys/${keyId}/spend`);
      const data = await response.json();
      setSpendMap(prev => ({
        ...prev,
        [keyId]: data
      }));
    } catch (error) {
      console.error('Error loading spend:', error);
      toast({
        title: 'Error',
        description: 'Failed to load spend information',
        variant: 'destructive',
      });
    }
  }, [toast]);

  // Update budget period
  const updateBudget = useCallback(async (keyId: number, budgetDuration: string) => {
    setIsUpdatingBudget(true);
    try {
      await post(`/private-ai-keys/${keyId}/budget-period`, { budget_duration: budgetDuration });
      await loadSpend(keyId);
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
  }, [toast, loadSpend]);

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
    onSuccess: async (data) => {
      await queryClient.invalidateQueries({ queryKey: ['private-ai-keys'] });
      setIsCreateDialogOpen(false);
      setSelectedRegion('');
      setKeyName('');
      // Determine key type based on returned data
      const newKeyType = data.litellm_token && data.database_name ? 'full' :
                        data.litellm_token ? 'llm' : 'vector';
      setKeyType(newKeyType);
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

  const handleCreateKey = () => {
    if (!selectedRegion || !keyName) return;

    const region = regions.find(r => r.name === selectedRegion);
    if (!region) return;

    createKeyMutation.mutate({
      region_id: region.id,
      name: keyName,
      key_type: keyType
    });
  };

  useEffect(() => {
    void fetchRegions();
    void fetchKeys();
  }, [fetchRegions, fetchKeys]);

  return (
    <div className="space-y-6">
      <div className="flex justify-between items-center">
        <h1 className="text-3xl font-bold">Private AI Keys</h1>
        {user?.role !== 'read_only' && (
          <Dialog open={isCreateDialogOpen} onOpenChange={setIsCreateDialogOpen}>
            <DialogTrigger asChild>
              <Button>Create Private AI Key</Button>
            </DialogTrigger>
            <DialogContent>
              <DialogHeader>
                <DialogTitle>Create Private AI Key</DialogTitle>
                <DialogDescription>
                  Select a region and provide a name for your new private AI key.
                </DialogDescription>
              </DialogHeader>
              <div className="py-4 space-y-4">
                <div className="space-y-2">
                  <label className="text-sm font-medium">Name <span className="text-red-500">*</span></label>
                  <Input
                    value={keyName}
                    onChange={(e) => setKeyName(e.target.value)}
                    placeholder="My AI Key"
                    required
                  />
                  <p className="text-sm text-muted-foreground">
                    A descriptive name to help you identify this key
                  </p>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Type <span className="text-red-500">*</span></label>
                  <Select value={keyType} onValueChange={(value: 'full' | 'llm' | 'vector') => setKeyType(value)}>
                    <SelectTrigger>
                      <SelectValue placeholder="Select a type" />
                    </SelectTrigger>
                    <SelectContent>
                      <SelectItem value="full">Full Key (LLM + Vector DB)</SelectItem>
                      <SelectItem value="llm">LLM Token Only</SelectItem>
                      <SelectItem value="vector">Vector DB Only</SelectItem>
                    </SelectContent>
                  </Select>
                  <p className="text-sm text-muted-foreground">
                    Choose whether to create a full key with both LLM and Vector DB access, or just one component
                  </p>
                </div>
                <div className="space-y-2">
                  <label className="text-sm font-medium">Region <span className="text-red-500">*</span></label>
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
              </div>
              <DialogFooter>
                <Button
                  onClick={handleCreateKey}
                  disabled={!selectedRegion || !keyName || createKeyMutation.isPending}
                >
                  {createKeyMutation.isPending ? (
                    <>
                      <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                      Creating...
                    </>
                  ) : (
                    'Create Key'
                  )}
                </Button>
              </DialogFooter>
            </DialogContent>
          </Dialog>
        )}
      </div>

      <PrivateAIKeysTable
        keys={privateAIKeys}
        onDelete={deleteKey}
        isLoading={createKeyMutation.isPending}
        showOwner={false}
        allowModification={false}
        spendMap={spendMap}
        onLoadSpend={loadSpend}
        onUpdateBudget={updateBudget}
        isDeleting={isDeleting}
        isUpdatingBudget={isUpdatingBudget}
      />

      {privateAIKeys.length === 0 && (
        <Card>
          <CardContent className="p-6">
            <p className="text-center text-muted-foreground">
              No private AI keys found. Keys will appear here once they are created.
            </p>
          </CardContent>
        </Card>
      )}
    </div>
  );
}