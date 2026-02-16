import { useQuery, useMutation, useQueryClient } from '@tanstack/react-query';
import { get, post, put, del } from '@/utils/api';
import { useToast } from '@/hooks/use-toast';
import { Team } from '@/types/team';
import { Product } from '@/types/product';

export function useTeams(includeDeleted: boolean = false) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Queries
  const teamsQuery = useQuery<Team[]>({
    queryKey: ['teams', includeDeleted],
    queryFn: async () => {
      const response = await get(`/teams?include_deleted=${includeDeleted}`);
      return response.json();
    },
  });

  // Mutations
  const createTeamMutation = useMutation({
    mutationFn: async (data: { name: string; admin_email: string }) => {
      const response = await post('/teams', data);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      toast({ title: 'Success', description: 'Team created successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const updateTeamMutation = useMutation({
    mutationFn: async ({ id, data }: { id: string | number; data: Partial<Team> }) => {
      const response = await put(`/teams/${id}`, data);
      return response.json();
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team', variables.id.toString()] });
      toast({ title: 'Success', description: 'Team updated successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const deleteTeamMutation = useMutation({
    mutationFn: async (id: string | number) => {
      const response = await del(`/teams/${id}`);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      toast({ title: 'Success', description: 'Team deleted successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const restoreTeamMutation = useMutation({
    mutationFn: async (id: string | number) => {
      const response = await post(`/teams/${id}/restore`, {});
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      toast({ title: 'Success', description: 'Team restored successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const extendTrialMutation = useMutation({
    mutationFn: async (id: string | number) => {
      const response = await post(`/teams/${id}/extend-trial`, {});
      return response.json();
    },
    onSuccess: (_, id) => {
      queryClient.invalidateQueries({ queryKey: ['team', id.toString()] });
      toast({ title: 'Success', description: 'Trial extended successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const mergeTeamsMutation = useMutation({
    mutationFn: async (data: {
      targetTeamId: string | number;
      sourceTeamId: string | number;
      conflictResolutionStrategy: 'delete' | 'rename' | 'cancel';
      renameSuffix?: string;
    }) => {
      const payload = {
        source_team_id: Number(data.sourceTeamId),
        conflict_resolution_strategy: data.conflictResolutionStrategy,
        rename_suffix: data.renameSuffix,
      };
      const response = await post(`/teams/${data.targetTeamId}/merge`, payload);
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      toast({ title: 'Success', description: 'Teams merged successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const subscribeToProductMutation = useMutation({
    mutationFn: async ({ teamId, productId }: { teamId: string | number; productId: string }) => {
      const response = await post(`/billing/teams/${teamId}/subscriptions`, { product_id: productId });
      return response.json();
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team', variables.teamId.toString()] });
      queryClient.invalidateQueries({ queryKey: ['team-products', variables.teamId.toString()] });
      toast({ title: 'Success', description: 'Subscribed to product successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const cancelSubscriptionMutation = useMutation({
    mutationFn: async ({ teamId, productId }: { teamId: string | number; productId: string }) => {
      const response = await del(`/billing/teams/${teamId}/subscription/${productId}`);
      return response.json();
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team', variables.teamId.toString()] });
      queryClient.invalidateQueries({ queryKey: ['team-products', variables.teamId.toString()] });
      toast({ title: 'Success', description: 'Subscription canceled successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const addUserToTeamMutation = useMutation({
    mutationFn: async ({ userId, teamId }: { userId: string | number; teamId: string | number }) => {
      const response = await post(`/users/${userId}/add-to-team`, { team_id: Number(teamId) });
      return response.json();
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team', variables.teamId.toString()] });
      toast({ title: 'Success', description: 'User added to team' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const removeUserFromTeamMutation = useMutation({
    mutationFn: async ({ userId, teamId }: { userId: string | number; teamId: string | number }) => {
      const response = await post(`/users/${userId}/remove-from-team`, {});
      return response.json();
    },
    onSuccess: (_, variables) => {
      queryClient.invalidateQueries({ queryKey: ['teams'] });
      queryClient.invalidateQueries({ queryKey: ['team', variables.teamId.toString()] });
      toast({ title: 'Success', description: 'User removed from team' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  const resetLimitsMutation = useMutation({
    mutationFn: async (teamId: string | number) => {
      const response = await post(`/limits/teams/${teamId}/reset`, {});
      return response.json();
    },
    onSuccess: (_, teamId) => {
      queryClient.invalidateQueries({ queryKey: ['team-limits', teamId.toString()] });
      toast({ title: 'Success', description: 'Limits reset successfully' });
    },
    onError: (error: Error) => {
      toast({ title: 'Error', description: error.message, variant: 'destructive' });
    },
  });

  return {
    teams: teamsQuery.data ?? [],
    isLoading: teamsQuery.isLoading,
    createTeam: createTeamMutation.mutate,
    isCreating: createTeamMutation.isPending,
    updateTeam: updateTeamMutation.mutate,
    isUpdating: updateTeamMutation.isPending,
    deleteTeam: deleteTeamMutation.mutate,
    isDeleting: deleteTeamMutation.isPending,
    restoreTeam: restoreTeamMutation.mutate,
    isRestoring: restoreTeamMutation.isPending,
    extendTrial: extendTrialMutation.mutate,
    isExtendingTrial: extendTrialMutation.isPending,
    mergeTeams: mergeTeamsMutation.mutate,
    isMerging: mergeTeamsMutation.isPending,
    subscribeToProduct: subscribeToProductMutation.mutate,
    isSubscribing: subscribeToProductMutation.isPending,
    cancelSubscription: cancelSubscriptionMutation.mutate,
    isCancelingSubscription: cancelSubscriptionMutation.isPending,
    addUserToTeam: addUserToTeamMutation.mutate,
    isAddingUser: addUserToTeamMutation.isPending,
    removeUserFromTeam: removeUserFromTeamMutation.mutate,
    isRemovingUser: removeUserFromTeamMutation.isPending,
    resetLimits: resetLimitsMutation.mutate,
    isResettingLimits: resetLimitsMutation.isPending,
  };
}
