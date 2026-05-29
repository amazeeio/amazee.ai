import { Loader2 } from "lucide-react";
import { Collapsible, CollapsibleContent } from "@/components/ui/collapsible";
import { LimitsView, LimitedResource } from "@/components/ui/limits-view";
import {
  Table,
  TableBody,
  TableCell,
  TableHead,
  TableHeader,
  TableRow,
} from "@/components/ui/table";
import { Tabs, TabsContent, TabsList, TabsTrigger } from "@/components/ui/tabs";
import { useToast } from "@/hooks/use-toast";
import { PrivateAIKey } from "@/types/private-ai-key";
import { Region } from "@/types/region";
import { SpendInfo } from "@/types/spend";
import { get, post } from "@/utils/api";
import { useQuery, useMutation, useQueryClient } from "@tanstack/react-query";

interface UserExpansionRowProps {
  userId: string;
  isExpanded: boolean;
}

export function UserExpansionRow({
  userId,
  isExpanded,
}: UserExpansionRowProps) {
  const { toast } = useToast();
  const queryClient = useQueryClient();

  // Get user AI keys when expanded
  const { data: userAIKeys = [], isLoading: isLoadingUserAIKeys } = useQuery<
    PrivateAIKey[]
  >({
    queryKey: ["user-ai-keys", userId],
    queryFn: async () => {
      const response = await get(`/private-ai-keys?owner_id=${userId}`);
      return response.json();
    },
    enabled: isExpanded,
  });

  // Fetch regions for spend lookup
  const { data: regions = [] } = useQuery<Region[]>({
    queryKey: ["regions"],
    queryFn: async () => {
      const response = await get("/regions");
      return response.json();
    },
    enabled: isExpanded && userAIKeys.length > 0,
  });

  // Get spend data for each key
  const { data: spendMap = {} } = useQuery<Record<string, SpendInfo>>({
    queryKey: ["user-ai-keys-spend", userId, userAIKeys],
    queryFn: async () => {
      if (userAIKeys.length === 0) return {};

      const spendData: Record<string, SpendInfo> = {};

      // Group keys by team+region combo
      const teamRegionMap = new Map<
        string,
        { regionName: string; teamId: number; keyIds: number[] }
      >();
      const noTeamKeys: PrivateAIKey[] = [];

      for (const key of userAIKeys) {
        if (key.team_id && key.region) {
          const comboKey = `${key.region}:${key.team_id}`;
          if (!teamRegionMap.has(comboKey)) {
            teamRegionMap.set(comboKey, {
              regionName: key.region,
              teamId: key.team_id,
              keyIds: [],
            });
          }
          teamRegionMap.get(comboKey)!.keyIds.push(key.id);
        } else {
          noTeamKeys.push(key);
        }
      }

      // Fetch spend per team+region combo
      for (const [, { regionName, teamId, keyIds }] of teamRegionMap) {
        try {
          const matchedRegion = regions.find((r) => r.name === regionName);
          if (matchedRegion) {
            const response = await get(
              `/spend/${matchedRegion.id}/team/${teamId}`,
            );
            const data = await response.json();
            // Map TeamSpendResponse to SpendInfo
            const spendInfo = {
              ...data,
              spend: data.total_spend ?? 0,
              max_budget: data.total_budget,
            };
            for (const keyId of keyIds) {
              spendData[keyId.toString()] = spendInfo;
            }
          }
        } catch (error) {
          console.error(
            `Failed to fetch spend for team ${teamId} region ${regionName}:`,
            error,
          );
        }
      }

      // Fallback for keys without team_id
      for (const key of noTeamKeys) {
        try {
          const response = await get(`/private-ai-keys/${key.id}/spend`);
          const spendInfo = await response.json();
          spendData[key.id.toString()] = spendInfo;
        } catch (error) {
          console.error(`Failed to fetch spend data for key ${key.id}:`, error);
        }
      }

      return spendData;
    },
    enabled: isExpanded && userAIKeys.length > 0 && regions.length > 0,
  });

  // Get user limits when expanded
  const { data: userLimits = [], isLoading: isLoadingUserLimits } = useQuery<
    LimitedResource[]
  >({
    queryKey: ["user-limits", userId],
    queryFn: async () => {
      const response = await get(`/limits/users/${userId}`);
      return response.json();
    },
    enabled: isExpanded,
  });

  const resetAllUserLimitsMutation = useMutation({
    mutationFn: async (userId: string) => {
      const response = await post(`/limits/users/${userId}/reset`, {});
      return response.json();
    },
    onSuccess: () => {
      queryClient.invalidateQueries({ queryKey: ["user-limits", userId] });
      toast({
        title: "Success",
        description: "All user limits reset successfully",
      });
    },
    onError: (error: Error) => {
      toast({
        title: "Error",
        description: error.message,
        variant: "destructive",
      });
    },
  });

  return (
    <TableRow>
      <TableCell colSpan={8} className="p-0">
        <Collapsible open={isExpanded}>
          <CollapsibleContent className="p-4 bg-muted/30">
            {isLoadingUserAIKeys || isLoadingUserLimits ? (
              <div className="flex justify-center items-center py-8">
                <Loader2 className="h-8 w-8 animate-spin" />
              </div>
            ) : (
              <div className="space-y-6">
                <Tabs defaultValue="ai-keys">
                  <TabsList>
                    <TabsTrigger value="ai-keys">AI Keys</TabsTrigger>
                    <TabsTrigger value="limits">Limits</TabsTrigger>
                  </TabsList>
                  <TabsContent value="ai-keys" className="mt-4">
                    <div className="space-y-4">
                      <h3 className="text-lg font-medium">AI Keys</h3>
                      {userAIKeys.length > 0 ? (
                        <div className="rounded-md border">
                          <Table>
                            <TableHeader>
                              <TableRow>
                                <TableHead>Name</TableHead>
                                <TableHead>Region</TableHead>
                                <TableHead>Database</TableHead>
                                <TableHead>Created At</TableHead>
                                <TableHead>Spend</TableHead>
                                <TableHead>Budget</TableHead>
                              </TableRow>
                            </TableHeader>
                            <TableBody>
                              {userAIKeys.map((key) => {
                                const spendInfo = spendMap[key.id.toString()];
                                return (
                                  <TableRow key={key.id}>
                                    <TableCell>{key.name}</TableCell>
                                    <TableCell>{key.region}</TableCell>
                                    <TableCell>{key.database_name}</TableCell>
                                    <TableCell>
                                      {new Date(
                                        key.created_at,
                                      ).toLocaleDateString()}
                                    </TableCell>
                                    <TableCell>
                                      {spendInfo ? (
                                        <span>
                                          ${spendInfo.spend.toFixed(2)}
                                        </span>
                                      ) : (
                                        <span className="text-muted-foreground">
                                          Loading...
                                        </span>
                                      )}
                                    </TableCell>
                                    <TableCell>
                                      {spendInfo?.max_budget ? (
                                        <span>
                                          ${spendInfo.max_budget.toFixed(2)}
                                        </span>
                                      ) : (
                                        <span className="text-muted-foreground">
                                          No limit
                                        </span>
                                      )}
                                    </TableCell>
                                  </TableRow>
                                );
                              })}
                            </TableBody>
                          </Table>
                        </div>
                      ) : (
                        <div className="text-center py-8 border rounded-md">
                          <p className="text-muted-foreground">
                            No AI keys found for this user.
                          </p>
                        </div>
                      )}
                    </div>
                  </TabsContent>
                  <TabsContent value="limits" className="mt-4">
                    <LimitsView
                      limits={userLimits}
                      isLoading={isLoadingUserLimits}
                      ownerType="user"
                      ownerId={userId}
                      queryKey={["user-limits", userId]}
                      showResetAll={true}
                      onResetAll={() =>
                        resetAllUserLimitsMutation.mutate(userId)
                      }
                      isResettingAll={resetAllUserLimitsMutation.isPending}
                    />
                  </TabsContent>
                </Tabs>
              </div>
            )}
          </CollapsibleContent>
        </Collapsible>
      </TableCell>
    </TableRow>
  );
}
