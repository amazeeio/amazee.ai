import { useQuery } from '@tanstack/react-query';
import { Button } from '@/components/ui/button';
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from '@/components/ui/table';
import {
  Collapsible,
  CollapsibleContent,
} from '@/components/ui/collapsible';
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs';
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from '@/components/ui/card';
import { Badge } from '@/components/ui/badge';
import { DeleteConfirmationDialog } from '@/components/ui/delete-confirmation-dialog';
import { LimitsView, LimitedResource } from '@/components/ui/limits-view';
import { Loader2, UserPlus, Plus } from 'lucide-react';
import { get } from '@/utils/api';
import { TableActionButtons } from '@/components/ui/table-action-buttons';
import { Team } from '@/types/team';
import { Product } from '@/types/product';
import { PrivateAIKey } from '@/types/private-ai-key';
import { SpendInfo } from '@/types/spend';
import { User } from '@/types/user';
import { useTeams } from '@/hooks/use-teams';

interface TeamExpansionRowProps {
  teamId: string;
  isExpanded: boolean;
  includeDeleted: boolean;
  onEdit: (team: Team) => void;
  onAddUser: (teamId: string) => void;
  onCreateUser: (teamId: string) => void;
  onSubscribe: (teamId: string) => void;
}

export function TeamExpansionRow({
  teamId,
  isExpanded,
  includeDeleted,
  onEdit,
  onAddUser,
  onCreateUser,
  onSubscribe
}: TeamExpansionRowProps) {
  const { 
    removeUserFromTeam, isRemovingUser,
    extendTrial, isExtendingTrial,
    restoreTeam, isRestoring,
    deleteTeam, isDeleting,
    cancelSubscription, isCancelingSubscription,
    resetLimits, isResettingLimits
  } = useTeams();

  const { data: expandedTeam, isLoading: isLoadingTeamDetails } = useQuery<Team>({
    queryKey: ['team', teamId, includeDeleted],
    queryFn: async () => {
      const response = await get(`/teams/${teamId}?include_deleted=${includeDeleted}`);
      return response.json();
    },
    enabled: isExpanded,
  });

  const { data: teamProducts = [], isLoading: isLoadingTeamProducts } = useQuery<Product[]>({
    queryKey: ['team-products', teamId],
    queryFn: async () => {
      const response = await get(`/products?team_id=${teamId}`);
      return response.json();
    },
    enabled: isExpanded,
  });

  const { data: teamAIKeys = [], isLoading: isLoadingTeamAIKeys } = useQuery<PrivateAIKey[]>({
    queryKey: ['team-ai-keys', teamId],
    queryFn: async () => {
      const response = await get(`/private-ai-keys?team_id=${teamId}`);
      return response.json();
    },
    enabled: isExpanded,
  });

  const { data: teamLimits = [], isLoading: isLoadingTeamLimits } = useQuery<LimitedResource[]>({
    queryKey: ['team-limits', teamId],
    queryFn: async () => {
      const response = await get(`/limits/teams/${teamId}`);
      return response.json();
    },
    enabled: isExpanded,
  });

  const { data: usersMap = {} } = useQuery<Record<string, User>>({
    queryKey: ['users-map'],
    queryFn: async () => {
      const response = await get('/users');
      const users: User[] = await response.json();
      return users.reduce((acc, user) => ({ ...acc, [user.id.toString()]: user }), {});
    },
    enabled: isExpanded,
  });

  const { data: spendMap = {} } = useQuery<Record<string, SpendInfo>>({
    queryKey: ['team-ai-keys-spend', teamId, teamAIKeys],
    queryFn: async () => {
      if (teamAIKeys.length === 0) return {};
      const spendData: Record<string, SpendInfo> = {};
      for (const key of teamAIKeys) {
        try {
          const response = await get(`/private-ai-keys/${key.id}/spend`);
          spendData[key.id.toString()] = await response.json();
        } catch (error) {
          console.error(`Failed to fetch spend data for key ${key.id}:`, error);
        }
      }
      return spendData;
    },
    enabled: isExpanded && teamAIKeys.length > 0,
  });

  const isTeamExpired = (team: Team): boolean => {
    if (team.products && team.products.some(product => product.active)) return false;
    const thirtyDaysAgo = new Date();
    thirtyDaysAgo.setDate(thirtyDaysAgo.getDate() - 30);
    const createdAt = new Date(team.created_at);
    if (createdAt >= thirtyDaysAgo) return false;
    if (!team.last_payment) return true;
    return new Date(team.last_payment) < thirtyDaysAgo;
  };

  return (
    <TableRow>
      <TableCell colSpan={7} className="p-0">
        <Collapsible open={isExpanded}>
          <CollapsibleContent className="p-4 bg-muted/30">
            {isLoadingTeamDetails ? (
              <div className="flex justify-center items-center py-8">
                <Loader2 className="h-8 w-8 animate-spin" />
              </div>
            ) : expandedTeam ? (
              <div className="space-y-6">
                <Tabs defaultValue="details">
                  <TabsList>
                    <TabsTrigger value="details">Team Details</TabsTrigger>
                    <TabsTrigger value="users">Users</TabsTrigger>
                    <TabsTrigger value="products">Products</TabsTrigger>
                    <TabsTrigger value="shared-keys">Shared Keys</TabsTrigger>
                    <TabsTrigger value="limits">Limits</TabsTrigger>
                  </TabsList>
                  <TabsContent value="details" className="mt-4">
                    <Card>
                      <CardHeader>
                        <CardTitle>Team Information</CardTitle>
                        <CardDescription>Detailed information about the team</CardDescription>
                      </CardHeader>
                      <CardContent className="space-y-4">
                        <div className="grid grid-cols-2 gap-4">
                          <div><p className="text-sm font-medium text-muted-foreground">Name</p><p>{expandedTeam.name}</p></div>
                          <div><p className="text-sm font-medium text-muted-foreground">Admin Email</p><p>{expandedTeam.admin_email}</p></div>
                          <div><p className="text-sm font-medium text-muted-foreground">Phone</p><p>{expandedTeam.phone}</p></div>
                          <div><p className="text-sm font-medium text-muted-foreground">Billing Address</p><p>{expandedTeam.billing_address}</p></div>
                          <div><p className="text-sm font-medium text-muted-foreground">Status</p><Badge variant={expandedTeam.is_active ? "default" : "destructive"}>{expandedTeam.is_active ? "Active" : "Inactive"}</Badge></div>
                          <div><p className="text-sm font-medium text-muted-foreground">Force User Keys</p><Badge variant={expandedTeam.force_user_keys ? "default" : "outline"}>{expandedTeam.force_user_keys ? "Enabled" : "Disabled"}</Badge></div>
                          {isTeamExpired(expandedTeam) && (<div><p className="text-sm font-medium text-muted-foreground">Expiration Status</p><Badge variant="destructive" className="bg-red-600 hover:bg-red-700">Expired</Badge></div>)}
                          {expandedTeam.is_always_free && (<div><p className="text-sm font-medium text-muted-foreground">Always Free Status</p><Badge variant="default" className="bg-green-500 hover:bg-green-600">Always Free</Badge></div>)}
                          <div><p className="text-sm font-medium text-muted-foreground">Created At</p><p>{new Date(expandedTeam.created_at).toLocaleString()}</p></div>
                          {expandedTeam.updated_at && (<div><p className="text-sm font-medium text-muted-foreground">Updated At</p><p>{new Date(expandedTeam.updated_at).toLocaleString()}</p></div>)}
                          {expandedTeam.last_payment && (<div><p className="text-sm font-medium text-muted-foreground">Last Payment</p><p>{new Date(expandedTeam.last_payment).toLocaleString()}</p></div>)}
                        </div>
                                                <div className="flex justify-end space-x-2 mt-4">
                                                  {expandedTeam.deleted_at ? (
                                                    <Button variant="default" onClick={() => restoreTeam(expandedTeam.id)} disabled={isRestoring}>
                                                      {isRestoring ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Restore Team
                                                    </Button>
                                                  ) : (
                                                    <>
                                                      <Button variant="outline" onClick={() => onEdit(expandedTeam)}>Edit Team</Button>
                                                      <Button variant="outline" onClick={() => onSubscribe(expandedTeam.id)}>Subscribe to Product</Button>
                                                      <Button variant="outline" onClick={() => extendTrial(expandedTeam.id)} disabled={isExtendingTrial}>
                                                        {isExtendingTrial ? <Loader2 className="mr-2 h-4 w-4 animate-spin" /> : null} Extend Trial
                                                      </Button>
                                                      {(!expandedTeam.users || expandedTeam.users.length === 0) && (
                                                        <DeleteConfirmationDialog title="Delete Team" description="Are you sure you want to delete this team? This action cannot be undone." triggerText="Delete Team" onConfirm={() => deleteTeam(expandedTeam.id)} isLoading={isDeleting} size="default" />
                                                      )}
                                                    </>
                                                  )}
                                                </div>
                                              </CardContent>
                                            </Card>
                                          </TabsContent>
                                          <TabsContent value="users" className="mt-4">
                                            <div className="flex justify-between items-center mb-4">
                                              <h3 className="text-lg font-medium">Team Users</h3>
                                              <div className="space-x-2">
                                                <Button size="sm" onClick={() => onAddUser(expandedTeam.id)}><UserPlus className="mr-2 h-4 w-4" />Add Existing User</Button>
                                                <Button size="sm" onClick={() => onCreateUser(expandedTeam.id)}><Plus className="mr-2 h-4 w-4" />Create New User</Button>
                                              </div>
                                            </div>
                                            {expandedTeam.users && expandedTeam.users.length > 0 ? (
                                              <div className="rounded-md border">
                                                <Table>
                                                  <TableHeader>
                                                    <TableRow><TableHead>Email</TableHead><TableHead>Role</TableHead><TableHead>Status</TableHead><TableHead>Admin</TableHead><TableHead className="text-right">Actions</TableHead></TableRow>
                                                  </TableHeader>
                                                  <TableBody>
                                                    {expandedTeam.users.map((user) => (
                                                      <TableRow key={user.id}>
                                                        <TableCell>{user.email}</TableCell><TableCell>{user.role || 'User'}</TableCell>
                                                        <TableCell><Badge variant={user.is_active ? "default" : "destructive"}>{user.is_active ? "Active" : "Inactive"}</Badge></TableCell>
                                                        <TableCell><Badge variant={user.is_admin ? "default" : "outline"}>{user.is_admin ? "Yes" : "No"}</Badge></TableCell>
                                                        <TableCell className="text-right">
                                                          <TableActionButtons showEdit={false} onDelete={() => removeUserFromTeam({ userId: user.id, teamId })} deleteTitle="Remove User" deleteDescription="Are you sure you want to remove this user from the team?" deleteText="Remove" deleteConfirmText="Remove" isDeleting={isRemovingUser} className="justify-end" />
                                                        </TableCell>
                                                      </TableRow>
                                                    ))}
                                                  </TableBody>
                                                </Table>
                                              </div>
                                            ) : (
                                              <div className="text-center py-8 border rounded-md">
                                                <p className="text-muted-foreground">No users in this team yet.</p>
                                                <p className="text-sm text-muted-foreground mt-2">Add existing users or create new ones to get started.</p>
                                              </div>
                                            )}
                                          </TabsContent>
                                          <TabsContent value="products" className="mt-4">
                                            <div className="space-y-4">
                                              {isLoadingTeamProducts ? (
                                                <div className="flex justify-center items-center py-8"><Loader2 className="h-8 w-8 animate-spin" /></div>
                                              ) : teamProducts.length > 0 ? (
                                                <div className="rounded-md border">
                                                  <Table>
                                                    <TableHeader>
                                                      <TableRow><TableHead>Name</TableHead><TableHead>User Count</TableHead><TableHead>Keys/User</TableHead><TableHead>Total Keys</TableHead><TableHead>Service Keys</TableHead><TableHead>Budget/Key</TableHead><TableHead>RPM/Key</TableHead><TableHead>Vector DBs</TableHead><TableHead>Storage (GiB)</TableHead><TableHead>Renewal (Days)</TableHead><TableHead>Status</TableHead><TableHead className="text-right">Actions</TableHead></TableRow>
                                                    </TableHeader>
                                                    <TableBody>
                                                      {teamProducts.map((product) => (
                                                        <TableRow key={product.id}>
                                                          <TableCell>{product.name}</TableCell><TableCell>{product.user_count}</TableCell><TableCell>{product.keys_per_user}</TableCell><TableCell>{product.total_key_count}</TableCell><TableCell>{product.service_key_count}</TableCell><TableCell>${product.max_budget_per_key.toFixed(2)}</TableCell><TableCell>{product.rpm_per_key}</TableCell><TableCell>{product.vector_db_count}</TableCell><TableCell>{product.vector_db_storage}</TableCell><TableCell>{product.renewal_period_days}</TableCell>
                                                          <TableCell><Badge variant={product.active ? "default" : "destructive"}>{product.active ? "Active" : "Inactive"}</Badge></TableCell>
                                                          <TableCell className="text-right">
                                                            <DeleteConfirmationDialog title="Cancel Subscription" description={`Are you sure you want to cancel the subscription to "${product.name}"? This action cannot be undone and will immediately remove access to this product.`} triggerText="Cancel" onConfirm={() => cancelSubscription({ teamId: expandedTeam.id, productId: product.id })} isLoading={isCancelingSubscription} size="sm" />
                                                          </TableCell>
                                                        </TableRow>
                                                      ))}
                                                    </TableBody>
                                                  </Table>
                                                </div>
                                              ) : (
                                                <div className="text-center py-8 border rounded-md"><p className="text-muted-foreground">No products associated with this team.</p></div>
                                              )}
                                            </div>
                                          </TabsContent>
                                          <TabsContent value="shared-keys" className="mt-4">
                                            <div className="space-y-4">
                                              {isLoadingTeamAIKeys ? (
                                                <div className="flex justify-center items-center py-8"><Loader2 className="h-8 w-8 animate-spin" /></div>
                                              ) : teamAIKeys.length > 0 ? (
                                                <div className="rounded-md border">
                                                  <Table>
                                                    <TableHeader>
                                                      <TableRow><TableHead>Name</TableHead><TableHead>Owner</TableHead><TableHead>Region</TableHead><TableHead>Database</TableHead><TableHead>Created At</TableHead><TableHead>Spend</TableHead><TableHead>Budget</TableHead></TableRow>
                                                    </TableHeader>
                                                    <TableBody>
                                                      {teamAIKeys.map((key) => {
                                                        const spendInfo = spendMap[key.id.toString()];
                                                        const owner = usersMap[key.owner_id?.toString() || ''];
                                                        return (
                                                          <TableRow key={key.id}>
                                                            <TableCell>{key.name}</TableCell>
                                                            <TableCell>{key.owner_id ? (owner ? owner.email : `User ${key.owner_id}`) : key.team_id ? <span>(Team) {expandedTeam.name || 'Team (Shared)'}</span> : <span className="text-muted-foreground">Unknown</span>}</TableCell>
                                                            <TableCell>{key.region}</TableCell><TableCell>{key.database_name}</TableCell><TableCell>{new Date(key.created_at).toLocaleDateString()}</TableCell>
                                                            <TableCell>{spendInfo ? <span>${spendInfo.spend.toFixed(2)}</span> : <span className="text-muted-foreground">Loading...</span>}</TableCell>
                                                            <TableCell>{spendInfo?.max_budget ? <span>${spendInfo.max_budget.toFixed(2)}</span> : <span className="text-muted-foreground">No limit</span>}</TableCell>
                                                          </TableRow>
                                                        );
                                                      })}
                                                    </TableBody>
                                                  </Table>
                                                </div>
                                              ) : (
                                                <div className="text-center py-8 border rounded-md"><p className="text-muted-foreground">No shared AI keys found for this team.</p></div>
                                              )}
                                            </div>
                                          </TabsContent>
                                          <TabsContent value="limits" className="mt-4">
                                            <LimitsView limits={teamLimits} isLoading={isLoadingTeamLimits} ownerType="team" ownerId={expandedTeam.id} queryKey={['team-limits', expandedTeam.id]} showResetAll={true} onResetAll={() => resetLimits(expandedTeam.id)} isResettingAll={isResettingLimits} />
                                          </TabsContent>
                </Tabs>
              </div>
            ) : (
              <div className="text-center py-8"><p className="text-muted-foreground">Failed to load team details.</p></div>
            )}
          </CollapsibleContent>
        </Collapsible>
      </TableCell>
    </TableRow>
  );
}
