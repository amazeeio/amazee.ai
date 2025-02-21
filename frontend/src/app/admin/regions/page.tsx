'use client';

import { useState, useEffect, useCallback } from 'react';
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
import { useToast } from '@/hooks/use-toast';
import { Loader2 } from 'lucide-react';
import { get, post, del, put } from '@/utils/api';

interface Region {
  id: string;
  name: string;
  postgres_host: string;
  postgres_port: number;
  postgres_admin_user: string;
  postgres_admin_password: string;
  litellm_api_url: string;
  litellm_api_key: string;
  is_active: boolean;
}

export default function RegionsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingRegion, setIsAddingRegion] = useState(false);
  const [isEditingRegion, setIsEditingRegion] = useState(false);
  const [editingRegion, setEditingRegion] = useState<Region | null>(null);
  const [newRegion, setNewRegion] = useState({
    name: '',
    postgres_host: '',
    postgres_port: 5432,
    postgres_admin_user: '',
    postgres_admin_password: '',
    litellm_api_url: '',
    litellm_api_key: '',
  });
  const [regions, setRegions] = useState<Region[]>([]);

  // Queries
  const { isLoading: isLoadingRegions } = useQuery<Region[]>({
    queryKey: ['regions'],
    queryFn: async () => {
      const response = await get('regions/admin');
      const data = await response.json();
      setRegions(data);
      return data;
    },
  });

  // Mutations
  const createRegionMutation = useMutation({
    mutationFn: async (regionData: typeof newRegion) => {
      const response = await post('regions', regionData);
      const data = await response.json();
      setRegions([...regions, data]);
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regions'] });
      setIsAddingRegion(false);
      setNewRegion({
        name: '',
        postgres_host: '',
        postgres_port: 5432,
        postgres_admin_user: '',
        postgres_admin_password: '',
        litellm_api_url: '',
        litellm_api_key: '',
      });
      toast({
        title: 'Success',
        description: 'Region created successfully',
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

  const deleteRegionMutation = useMutation({
    mutationFn: async (regionId: string) => {
      await del(`regions/${regionId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regions'] });
      toast({
        title: 'Success',
        description: 'Region deleted successfully',
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

  const updateRegionMutation = useMutation({
    mutationFn: async (regionData: Region) => {
      type UpdateData = {
        name: string;
        postgres_host: string;
        postgres_port: number;
        postgres_admin_user: string;
        litellm_api_url: string;
        is_active: boolean;
        postgres_admin_password?: string;
        litellm_api_key?: string;
      };

      const updateData: UpdateData = {
        name: regionData.name,
        postgres_host: regionData.postgres_host,
        postgres_port: regionData.postgres_port,
        postgres_admin_user: regionData.postgres_admin_user,
        litellm_api_url: regionData.litellm_api_url,
        is_active: regionData.is_active,
      };

      // Only include passwords if they are not empty
      if (regionData.postgres_admin_password) {
        updateData.postgres_admin_password = regionData.postgres_admin_password;
      }
      if (regionData.litellm_api_key) {
        updateData.litellm_api_key = regionData.litellm_api_key;
      }

      const response = await put(`regions/${regionData.id}`, updateData);
      const data = await response.json();
      return data;
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['regions'] });
      setIsEditingRegion(false);
      setEditingRegion(null);
      toast({
        title: 'Success',
        description: 'Region updated successfully',
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

  const handleCreateRegion = async (e: React.FormEvent) => {
    e.preventDefault();
    try {
      const response = await post('regions', newRegion);
      const data = await response.json();
      setRegions([...regions, data]);
      setIsAddingRegion(false);
      setNewRegion({
        name: '',
        postgres_host: '',
        postgres_port: 5432,
        postgres_admin_user: '',
        postgres_admin_password: '',
        litellm_api_url: '',
        litellm_api_key: '',
      });
      toast({
        title: 'Success',
        description: 'Region created successfully',
      });
    } catch (error) {
      console.error('Error creating region:', error);
      toast({
        title: 'Error',
        description: 'Failed to create region',
        variant: 'destructive',
      });
    }
  };

  const fetchRegions = useCallback(async () => {
    try {
      const response = await get('regions/admin');
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
  }, [toast, setRegions]);

  useEffect(() => {
    void fetchRegions();
  }, [fetchRegions]);

  const handleEditRegion = (region: Region) => {
    setEditingRegion(region);
    setIsEditingRegion(true);
  };

  const handleUpdateRegion = async (e: React.FormEvent) => {
    e.preventDefault();
    if (!editingRegion) return;
    updateRegionMutation.mutate(editingRegion);
  };

  return (
    <div>
      {isLoadingRegions ? (
        <div className="flex items-center justify-center min-h-[400px]">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <h1 className="text-2xl font-bold">Regions</h1>
            <Dialog open={isAddingRegion} onOpenChange={setIsAddingRegion}>
              <DialogTrigger asChild>
                <Button>Add Region</Button>
              </DialogTrigger>
              <DialogContent className="max-w-2xl">
                <DialogHeader>
                  <DialogTitle>Add New Region</DialogTitle>
                  <DialogDescription>
                    Create a new region for hosting private AI databases.
                  </DialogDescription>
                </DialogHeader>
                <form onSubmit={handleCreateRegion} className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Name</label>
                      <Input
                        value={newRegion.name}
                        onChange={(e) => setNewRegion({ ...newRegion, name: e.target.value })}
                        placeholder="us-east-1"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Postgres Host</label>
                      <Input
                        value={newRegion.postgres_host}
                        onChange={(e) => setNewRegion({ ...newRegion, postgres_host: e.target.value })}
                        placeholder="db.example.com"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Postgres Port</label>
                      <Input
                        type="number"
                        value={newRegion.postgres_port}
                        onChange={(e) => setNewRegion({ ...newRegion, postgres_port: parseInt(e.target.value) })}
                        placeholder="5432"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Admin Username</label>
                      <Input
                        value={newRegion.postgres_admin_user}
                        onChange={(e) => setNewRegion({ ...newRegion, postgres_admin_user: e.target.value })}
                        placeholder="admin"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Admin Password</label>
                      <Input
                        type="password"
                        value={newRegion.postgres_admin_password}
                        onChange={(e) => setNewRegion({ ...newRegion, postgres_admin_password: e.target.value })}
                        placeholder="••••••••"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">LiteLLM API URL</label>
                      <Input
                        value={newRegion.litellm_api_url}
                        onChange={(e) => setNewRegion({ ...newRegion, litellm_api_url: e.target.value })}
                        placeholder="https://api.litellm.ai"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">LiteLLM API Key</label>
                      <Input
                        type="password"
                        value={newRegion.litellm_api_key}
                        onChange={(e) => setNewRegion({ ...newRegion, litellm_api_key: e.target.value })}
                        placeholder="••••••••"
                        required
                      />
                    </div>
                  </div>
                  <DialogFooter>
                    <Button
                      type="submit"
                      disabled={createRegionMutation.isPending}
                    >
                      {createRegionMutation.isPending ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Creating...
                        </>
                      ) : (
                        'Create Region'
                      )}
                    </Button>
                  </DialogFooter>
                </form>
              </DialogContent>
            </Dialog>
          </div>
          <div className="rounded-md border">
            <Table>
              <TableHeader>
                <TableRow>
                  <TableHead>Name</TableHead>
                  <TableHead>Postgres Host</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {regions.map((region) => (
                  <TableRow key={region.id}>
                    <TableCell>{region.name}</TableCell>
                    <TableCell>{region.postgres_host}</TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        region.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                      }`}>
                        {region.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </TableCell>
                    <TableCell className="space-x-2">
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() => handleEditRegion(region)}
                      >
                        Edit
                      </Button>
                      <AlertDialog>
                        <AlertDialogTrigger asChild>
                          <Button variant="destructive" size="sm">Delete</Button>
                        </AlertDialogTrigger>
                        <AlertDialogContent>
                          <AlertDialogHeader>
                            <AlertDialogTitle>Delete Region</AlertDialogTitle>
                            <AlertDialogDescription>
                              Are you sure you want to delete this region? This action cannot be undone.
                            </AlertDialogDescription>
                          </AlertDialogHeader>
                          <AlertDialogFooter>
                            <AlertDialogCancel>Cancel</AlertDialogCancel>
                            <AlertDialogAction
                              onClick={() => deleteRegionMutation.mutate(region.id)}
                              className="bg-destructive text-destructive-foreground hover:bg-destructive/90"
                            >
                              {deleteRegionMutation.isPending ? (
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
          <Dialog open={isEditingRegion} onOpenChange={setIsEditingRegion}>
            <DialogContent className="max-w-2xl">
              <DialogHeader>
                <DialogTitle>Edit Region</DialogTitle>
                <DialogDescription>
                  Update the region configuration.
                </DialogDescription>
              </DialogHeader>
              {editingRegion && (
                <form onSubmit={handleUpdateRegion} className="space-y-4">
                  <div className="grid grid-cols-2 gap-4">
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Name</label>
                      <Input
                        value={editingRegion.name}
                        onChange={(e) => setEditingRegion({ ...editingRegion, name: e.target.value })}
                        placeholder="us-east-1"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Postgres Host</label>
                      <Input
                        value={editingRegion.postgres_host}
                        onChange={(e) => setEditingRegion({ ...editingRegion, postgres_host: e.target.value })}
                        placeholder="db.example.com"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Postgres Port</label>
                      <Input
                        type="number"
                        value={editingRegion.postgres_port}
                        onChange={(e) => setEditingRegion({ ...editingRegion, postgres_port: parseInt(e.target.value) })}
                        placeholder="5432"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Admin Username</label>
                      <Input
                        value={editingRegion.postgres_admin_user}
                        onChange={(e) => setEditingRegion({ ...editingRegion, postgres_admin_user: e.target.value })}
                        placeholder="admin"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">Admin Password</label>
                      <Input
                        type="password"
                        value={editingRegion.postgres_admin_password}
                        onChange={(e) => setEditingRegion({ ...editingRegion, postgres_admin_password: e.target.value })}
                        placeholder="••••••••"
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">LiteLLM API URL</label>
                      <Input
                        value={editingRegion.litellm_api_url}
                        onChange={(e) => setEditingRegion({ ...editingRegion, litellm_api_url: e.target.value })}
                        placeholder="https://api.litellm.ai"
                        required
                      />
                    </div>
                    <div className="space-y-2">
                      <label className="text-sm font-medium">LiteLLM API Key</label>
                      <Input
                        type="password"
                        value={editingRegion.litellm_api_key}
                        onChange={(e) => setEditingRegion({ ...editingRegion, litellm_api_key: e.target.value })}
                        placeholder="••••••••"
                      />
                    </div>
                  </div>
                  <DialogFooter>
                    <Button
                      type="submit"
                      disabled={updateRegionMutation.isPending}
                    >
                      {updateRegionMutation.isPending ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Updating...
                        </>
                      ) : (
                        'Update Region'
                      )}
                    </Button>
                  </DialogFooter>
                </form>
              )}
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}