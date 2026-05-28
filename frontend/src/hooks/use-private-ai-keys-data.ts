import { PrivateAIKey } from "@/types/private-ai-key";
import { Region } from "@/types/region";
import { SpendInfo } from "@/types/spend";
import { User } from "@/types/user";
import { get } from "@/utils/api";
import { useQuery } from "@tanstack/react-query";

export function usePrivateAIKeysData(
  keys: PrivateAIKey[],
  loadedSpendKeys: Set<number>,
) {
  // Get unique team IDs from the keys
  const teamIds = Array.from(
    new Set(keys.filter((key) => key.team_id).map((key) => key.team_id)),
  );

  // Fetch team details for each team ID
  const { data: teamDetails = {} } = useQuery({
    queryKey: ["team-details", teamIds],
    queryFn: async () => {
      const teamPromises = teamIds.map(async (teamId) => {
        const response = await get(`teams/${teamId}`);
        const data = await response.json();
        return [teamId, data];
      });
      const teamResults = await Promise.all(teamPromises);
      return Object.fromEntries(teamResults);
    },
    enabled: teamIds.length > 0,
  });

  // Query to get all users for displaying emails
  const { data: usersMap = {} } = useQuery<Record<string, User>>({
    queryKey: ["users-map"],
    queryFn: async () => {
      const response = await get("users");
      const users: User[] = await response.json();
      return users.reduce(
        (acc: Record<string, User>, user: User) => ({
          ...acc,
          [user.id.toString()]: user,
        }),
        {},
      );
    },
  });

  // Query to get spend information for loaded keys
  // Fetches per team+region combo for keys with team_id, old endpoint for others
  const loadedSpendKeysArray = Array.from(loadedSpendKeys).sort();
  const { data: spendMap = {} } = useQuery<Record<number, SpendInfo>>({
    queryKey: ["private-ai-keys-spend", loadedSpendKeysArray],
    queryFn: async () => {
      const loadedKeys = keys.filter((k) => loadedSpendKeys.has(k.id));

      // Group keys by team+region combo
      const teamRegionMap = new Map<
        string,
        { regionName: string; teamId: number; keyIds: number[] }
      >();
      const noTeamKeys: PrivateAIKey[] = [];

      for (const key of loadedKeys) {
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

      const result: Record<number, SpendInfo> = {};

      // Fetch spend per team+region combo
      const teamSpendPromises = Array.from(teamRegionMap.values()).map(
        async ({ regionName, teamId, keyIds }) => {
          const matchedRegion = regions.find((r) => r.name === regionName);
          if (matchedRegion) {
            const response = await get(
              `spend/${matchedRegion.id}/team/${teamId}`,
            );
            const spendInfo: SpendInfo = await response.json();
            // Assign same team spend to all keys in this combo
            for (const keyId of keyIds) {
              result[keyId] = spendInfo;
            }
          }
        },
      );

      // Fetch spend for keys without team_id using old endpoint
      const noTeamPromises = noTeamKeys.map(async (key) => {
        const response = await get(`private-ai-keys/${key.id}/spend`);
        result[key.id] = await response.json();
      });

      await Promise.all([...teamSpendPromises, ...noTeamPromises]);
      return result;
    },
    enabled: loadedSpendKeysArray.length > 0,
  });

  // Fetch regions
  const { data: regions = [] } = useQuery<Region[]>({
    queryKey: ["regions"],
    queryFn: async () => {
      const response = await get("regions");
      const data = await response.json();
      return data;
    },
  });

  return {
    teamDetails,
    teamMembers: Object.values(usersMap),
    spendMap,
    regions,
  };
}
