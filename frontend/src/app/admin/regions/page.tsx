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
  TablePagination,
  useTablePagination,
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
import { TableActionButtons } from '@/components/ui/table-action-buttons';
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select';
import { Badge } from '@/components/ui/badge';
import { useToast } from '@/hooks/use-toast';
import { Loader2, Users, X } from 'lucide-react';
import { get, post, del, put } from '@/utils/api';

interface Team {
  id: number;
  name: string;
}

interface Region {
  id: string;
  name: string;
  label: string;
  postgres_host: string;
  postgres_port: number;
  postgres_admin_user: string;
  postgres_admin_password: string;
  litellm_api_url: string;
  litellm_api_key: string;
  is_active: boolean;
  is_dedicated: boolean;
}

export default function RegionsPage() {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [isAddingRegion, setIsAddingRegion] = useState(false);
  const [isEditingRegion, setIsEditingRegion] = useState(false);
  const [isManagingTeams, setIsManagingTeams] = useState(false);
  const [editingRegion, setEditingRegion] = useState<Region | null>(null);
  const [selectedRegionForTeams, setSelectedRegionForTeams] = useState<Region | null>(null);
  const [newRegion, setNewRegion] = useState({
    name: '',
    label: '',
    postgres_host: '',
    postgres_port: 5432,
    postgres_admin_user: '',
    postgres_admin_password: '',
    litellm_api_url: '',
    litellm_api_key: '',
    is_dedicated: false,
  });
  const [regions, setRegions] = useState<Region[]>([]);
  const [selectedTeamId, setSelectedTeamId] = useState<string>('');
  const [teamSearchQuery, setTeamSearchQuery] = useState<string>('');

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

  const { data: teams = [] } = useQuery<Team[]>({
    queryKey: ['teams'],
    queryFn: async () => {
      const response = await get('teams');
      return response.json();
    },
  });

  const { data: regionTeams = [], isLoading: isLoadingRegionTeams } = useQuery<Team[]>({
    queryKey: ['region-teams', selectedRegionForTeams?.id],
    queryFn: async () => {
      if (!selectedRegionForTeams?.id) return [];
      const response = await get(`regions/${selectedRegionForTeams.id}/teams`);
      return response.json();
    },
    enabled: !!selectedRegionForTeams?.id,
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
      queryClient.refetchQueries({ queryKey: ['regions'], exact: true });
      setIsAddingRegion(false);
      setNewRegion({
        name: '',
        label: '',
        postgres_host: '',
        postgres_port: 5432,
        postgres_admin_user: '',
        postgres_admin_password: '',
        litellm_api_url: '',
        litellm_api_key: '',
        is_dedicated: false,
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
      queryClient.refetchQueries({ queryKey: ['regions'], exact: true });
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
        label: string;
        postgres_host: string;
        postgres_port: number;
        postgres_admin_user: string;
        litellm_api_url: string;
        is_active: boolean;
        is_dedicated: boolean;
        postgres_admin_password?: string;
        litellm_api_key?: string;
      };

      const updateData: UpdateData = {
        name: regionData.name,
        label: regionData.label,
        postgres_host: regionData.postgres_host,
        postgres_port: regionData.postgres_port,
        postgres_admin_user: regionData.postgres_admin_user,
        litellm_api_url: regionData.litellm_api_url,
        is_active: regionData.is_active,
        is_dedicated: regionData.is_dedicated,
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
      queryClient.refetchQueries({ queryKey: ['regions'], exact: true });
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

  const assignTeamMutation = useMutation({
    mutationFn: async ({ regionId, teamId }: { regionId: string; teamId: string }) => {
      await post(`regions/${regionId}/teams/${teamId}`, {});
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['region-teams', selectedRegionForTeams?.id] });
      queryClient.refetchQueries({ queryKey: ['region-teams', selectedRegionForTeams?.id], exact: true });
      setSelectedTeamId('');
      toast({
        title: 'Success',
        description: 'Team assigned to region successfully',
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

  const removeTeamMutation = useMutation({
    mutationFn: async ({ regionId, teamId }: { regionId: string; teamId: string }) => {
      await del(`regions/${regionId}/teams/${teamId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['region-teams', selectedRegionForTeams?.id] });
      queryClient.refetchQueries({ queryKey: ['region-teams', selectedRegionForTeams?.id], exact: true });
      toast({
        title: 'Success',
        description: 'Team removed from region successfully',
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
    createRegionMutation.mutate(newRegion);
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

  const handleManageTeams = (region: Region) => {
    setSelectedRegionForTeams(region);
    setIsManagingTeams(true);
    setTeamSearchQuery(''); // Clear search when opening dialog
  };

  const handleAssignTeam = () => {
    if (!selectedTeamId || !selectedRegionForTeams) return;
    assignTeamMutation.mutate({
      regionId: selectedRegionForTeams.id,
      teamId: selectedTeamId,
    });
  };

  const handleRemoveTeam = (teamId: string) => {
    if (!selectedRegionForTeams) return;
    removeTeamMutation.mutate({
      regionId: selectedRegionForTeams.id,
      teamId,
    });
  };

  // Get available teams (teams not already assigned to this region)
  const availableTeams = teams.filter(
    team => !regionTeams.some(regionTeam => regionTeam.id === team.id)
  );

  // Filter region teams based on search query
  const filteredRegionTeams = regionTeams.filter(team =>
    team.name.toLowerCase().includes(teamSearchQuery.toLowerCase())
  );

  // Pagination
  const {
    currentPage,
    pageSize,
    totalPages,
    totalItems,
    paginatedData,
    goToPage,
    changePageSize,
  } = useTablePagination(regions, 10);

  return (
    <div className="space-y-4">
      {isLoadingRegions ? (
        <div className="flex items-center justify-center min-h-[400px]">
          <Loader2 className="h-8 w-8 animate-spin" />
        </div>
      ) : (
        <>
          <div className="flex items-center justify-between">
            <h1 className="text-3xl font-bold">Regions</h1>
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
                      <label className="text-sm font-medium">Label</label>
                      <Input
                        value={newRegion.label}
                        onChange={(e) => setNewRegion({ ...newRegion, label: e.target.value })}
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
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="is_dedicated"
                      checked={newRegion.is_dedicated}
                      onChange={(e) =>
                        setNewRegion({ ...newRegion, is_dedicated: e.target.checked })
                      }
                      className="h-4 w-4 rounded border-gray-300"
                    />
                    <label htmlFor="is_dedicated" className="text-sm font-medium">
                      Dedicated Region (can be assigned to specific teams)
                    </label>
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
                  <TableHead>Label</TableHead>
                  <TableHead>Postgres Host</TableHead>
                  <TableHead>Type</TableHead>
                  <TableHead>Status</TableHead>
                  <TableHead>Teams</TableHead>
                  <TableHead>Actions</TableHead>
                </TableRow>
              </TableHeader>
              <TableBody>
                {paginatedData.map((region) => (
                  <TableRow key={region.id}>
                    <TableCell>{region.name}</TableCell>
                    <TableCell>{region.label}</TableCell>
                    <TableCell>{region.postgres_host}</TableCell>
                    <TableCell>
                      <Badge variant={region.is_dedicated ? "default" : "secondary"}>
                        {region.is_dedicated ? 'Dedicated' : 'Shared'}
                      </Badge>
                    </TableCell>
                    <TableCell>
                      <span className={`inline-flex items-center px-2.5 py-0.5 rounded-full text-xs font-medium ${
                        region.is_active ? 'bg-green-100 text-green-800' : 'bg-red-100 text-red-800'
                      }`}>
                        {region.is_active ? 'Active' : 'Inactive'}
                      </span>
                    </TableCell>
                    <TableCell>
                      {region.is_dedicated ? (
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() => handleManageTeams(region)}
                          className="flex items-center gap-2"
                        >
                          <Users className="h-4 w-4" />
                          Manage Teams
                        </Button>
                      ) : (
                        <span className="text-gray-500 text-sm">N/A</span>
                      )}
                    </TableCell>
                    <TableCell>
                      <TableActionButtons
                        onEdit={() => handleEditRegion(region)}
                        onDelete={() => deleteRegionMutation.mutate(region.id)}
                        deleteTitle="Delete Region"
                        deleteDescription="Are you sure you want to delete this region? This action cannot be undone."
                        isDeleting={deleteRegionMutation.isPending}
                      />
                    </TableCell>
                  </TableRow>
                ))}
              </TableBody>
            </Table>
          </div>

          <TablePagination
            currentPage={currentPage}
            totalPages={totalPages}
            pageSize={pageSize}
            totalItems={totalItems}
            onPageChange={goToPage}
            onPageSizeChange={changePageSize}
          />

          {/* Edit Region Dialog */}
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
                      <label className="text-sm font-medium">Label</label>
                      <Input
                        value={editingRegion.label}
                        onChange={(e) => setEditingRegion({ ...editingRegion, label: e.target.value })}
                        placeholder="US East 1"
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
                  <div className="flex items-center space-x-2">
                    <input
                      type="checkbox"
                      id="edit_is_dedicated"
                      checked={editingRegion.is_dedicated}
                      onChange={(e) =>
                        setEditingRegion({ ...editingRegion, is_dedicated: e.target.checked })
                      }
                      className="h-4 w-4 rounded border-gray-300"
                    />
                    <label htmlFor="edit_is_dedicated" className="text-sm font-medium">
                      Dedicated Region (can be assigned to specific teams)
                    </label>
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

          {/* Manage Teams Dialog */}
          <Dialog open={isManagingTeams} onOpenChange={(open) => {
            setIsManagingTeams(open);
            if (!open) {
              setTeamSearchQuery(''); // Clear search when closing dialog
            }
          }}>
            <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
              <DialogHeader>
                <DialogTitle>Manage Teams for {selectedRegionForTeams?.name}</DialogTitle>
                <DialogDescription>
                  Assign or remove teams from this dedicated region.
                </DialogDescription>
              </DialogHeader>
              <div className="space-y-4">
                {/* Current Teams */}
                <div>
                  <div className="flex items-center justify-between mb-2">
                    <h3 className="text-sm font-medium">Assigned Teams ({regionTeams.length})</h3>
                    {regionTeams.length > 0 && (
                      <div className="flex items-center gap-2">
                        <Input
                          placeholder="Search teams..."
                          value={teamSearchQuery}
                          onChange={(e) => setTeamSearchQuery(e.target.value)}
                          className="h-8 w-48 text-sm"
                        />
                        {teamSearchQuery && (
                          <Button
                            variant="ghost"
                            size="sm"
                            onClick={() => setTeamSearchQuery('')}
                            className="h-8 w-8 p-0"
                          >
                            <X className="h-4 w-4" />
                          </Button>
                        )}
                      </div>
                    )}
                  </div>
                  {isLoadingRegionTeams ? (
                    <div className="flex items-center justify-center py-4">
                      <Loader2 className="h-4 w-4 animate-spin" />
                    </div>
                  ) : regionTeams.length === 0 ? (
                    <p className="text-sm text-gray-500">No teams assigned to this region.</p>
                  ) : filteredRegionTeams.length === 0 ? (
                    <p className="text-sm text-gray-500">No teams match your search.</p>
                  ) : (
                    <div className="border rounded-md" style={{ height: '200px', overflowY: 'auto' }}>
                      <div className="space-y-1 p-2">
                        {filteredRegionTeams.map((team) => (
                          <div key={team.id} className="flex items-center justify-between p-3 border rounded-md bg-gray-50 hover:bg-gray-100 transition-colors">
                            <span className="text-sm font-medium">{team.name}</span>
                            <Button
                              variant="outline"
                              size="sm"
                              onClick={() => handleRemoveTeam(team.id.toString())}
                              disabled={removeTeamMutation.isPending}
                              className="h-8 w-8 p-0"
                            >
                              <X className="h-4 w-4" />
                            </Button>
                          </div>
                        ))}
                      </div>
                    </div>
                  )}
                </div>

                {/* Assign New Team */}
                <div className="border-t pt-4">
                  <h3 className="text-sm font-medium mb-2">Assign New Team</h3>
                  <div className="flex gap-2">
                    <Select value={selectedTeamId} onValueChange={setSelectedTeamId}>
                      <SelectTrigger className="flex-1">
                        <SelectValue placeholder="Select a team" />
                      </SelectTrigger>
                      <SelectContent>
                        {availableTeams.map((team) => (
                          <SelectItem key={team.id} value={team.id.toString()}>
                            {team.name}
                          </SelectItem>
                        ))}
                      </SelectContent>
                    </Select>
                    <Button
                      onClick={handleAssignTeam}
                      disabled={!selectedTeamId || assignTeamMutation.isPending}
                    >
                      {assignTeamMutation.isPending ? (
                        <>
                          <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                          Assigning...
                        </>
                      ) : (
                        'Assign'
                      )}
                    </Button>
                  </div>
                </div>
              </div>
            </DialogContent>
          </Dialog>
        </>
      )}
    </div>
  );
}