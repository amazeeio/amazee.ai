import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog';
import { Combobox } from "@/components/ui/combobox";
import { Loader2, X } from 'lucide-react';
import { get, post, del } from '@/utils/api';
import { useToast } from '@/hooks/use-toast';
import { Region } from '@/types/region';
import { Team } from '@/types/team';

interface ManageRegionTeamsDialogProps {
  region: Region | null;
  open: boolean;
  onOpenChange: (open: boolean) => void;
  allTeams: Team[];
}

export function ManageRegionTeamsDialog({ region, open, onOpenChange, allTeams }: ManageRegionTeamsDialogProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();
  const [selectedTeamId, setSelectedTeamId] = useState<string>('');
  const [teamSearchQuery, setTeamSearchQuery] = useState<string>('');

  // Clear search when closing
  const handleOpenChange = (newOpen: boolean) => {
    if (!newOpen) {
      setTeamSearchQuery('');
    }
    onOpenChange(newOpen);
  };

  const { data: regionTeams = [], isLoading: isLoadingRegionTeams } = useQuery<Team[]>({
    queryKey: ['region-teams', region?.id],
    queryFn: async () => {
      if (!region?.id) return [];
      const response = await get(`regions/${region.id}/teams`);
      return response.json();
    },
    enabled: !!region?.id && open,
  });

  const assignTeamMutation = useMutation({
    mutationFn: async ({ regionId, teamId }: { regionId: string | number; teamId: string | number }) => {
      await post(`regions/${regionId}/teams/${teamId}`, {});
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['region-teams', region?.id] });
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
    mutationFn: async ({ regionId, teamId }: { regionId: string | number; teamId: string | number }) => {
      await del(`regions/${regionId}/teams/${teamId}`);
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['region-teams', region?.id] });
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

  const handleAssignTeam = () => {
    if (!selectedTeamId || !region) return;
    assignTeamMutation.mutate({
      regionId: region.id,
      teamId: selectedTeamId,
    });
  };

  const handleRemoveTeam = (teamId: string) => {
    if (!region) return;
    removeTeamMutation.mutate({
      regionId: region.id,
      teamId,
    });
  };

  // Get available teams (teams not already assigned to this region)
  const availableTeams = allTeams.filter(
    team => !regionTeams.some(regionTeam => regionTeam.id === team.id)
  );

  // Filter region teams based on search query
  const filteredRegionTeams = regionTeams.filter(team =>
    team.name.toLowerCase().includes(teamSearchQuery.toLowerCase())
  );

  if (!region) return null;

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="max-w-2xl max-h-[80vh] flex flex-col">
        <DialogHeader>
          <DialogTitle>Manage Teams for {region.name}</DialogTitle>
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
              <Combobox
                options={availableTeams.map(t => ({ value: t.id.toString(), label: t.name }))}
                value={selectedTeamId}
                onValueChange={setSelectedTeamId}
                placeholder="Select a team..."
                className="flex-1"
              />
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
  );
}

